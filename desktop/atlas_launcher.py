from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import platform
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse


LOOPBACK = "127.0.0.1"
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
ERROR_ALREADY_EXISTS = 183
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUPS = 3


def instance_name(state_dir: Path) -> str:
    fingerprint = hashlib.sha256(
        str(state_dir.resolve()).casefold().encode("utf-8")
    ).hexdigest()[:16]
    return f"Local\\AtlasDesktop-{fingerprint}"


class WindowsSingleInstance:
    """Use a per-state-directory mutex so double-clicks cannot start two backends."""

    def __init__(self, state_dir: Path) -> None:
        self.handle = None
        self.kernel32 = None
        self.acquired = True
        if os.name != "nt":
            return

        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        ctypes.set_last_error(0)
        handle = kernel32.CreateMutexW(None, False, instance_name(state_dir))
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            self.acquired = False
            return
        self.handle = handle
        self.kernel32 = kernel32

    def close(self) -> None:
        if self.handle is not None:
            self.kernel32.CloseHandle(self.handle)
            self.handle = None


def rotate_log(path: Path, max_bytes: int = LOG_MAX_BYTES, backups: int = LOG_BACKUPS) -> None:
    if backups < 1 or not path.is_file() or path.stat().st_size < max_bytes:
        return
    oldest = path.with_name(f"{path.name}.{backups}")
    oldest.unlink(missing_ok=True)
    for number in range(backups - 1, 0, -1):
        source = path.with_name(f"{path.name}.{number}")
        if source.is_file():
            source.replace(path.with_name(f"{path.name}.{number + 1}"))
    path.replace(path.with_name(f"{path.name}.1"))


class WindowsProcessJob:
    """Own child processes so Windows kills them if the launcher disappears."""

    def __init__(self) -> None:
        self.handle = None
        self.kernel32 = None
        if os.name != "nt":
            return

        from ctypes import wintypes

        class IoCounters(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class BasicLimitInformation(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class ExtendedLimitInformation(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimitInformation),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        information = ExtendedLimitInformation()
        information.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(
            handle, 9, ctypes.byref(information), ctypes.sizeof(information)
        ):
            error = ctypes.get_last_error()
            kernel32.CloseHandle(handle)
            raise ctypes.WinError(error)
        self.handle = handle
        self.kernel32 = kernel32

    @property
    def enabled(self) -> bool:
        return self.handle is not None

    def assign(self, process: subprocess.Popen) -> None:
        if not self.enabled:
            return
        if not self.kernel32.AssignProcessToJobObject(self.handle, process._handle):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self) -> None:
        if self.enabled:
            self.kernel32.CloseHandle(self.handle)
            self.handle = None


@dataclass(frozen=True)
class DesktopLayout:
    install_dir: Path
    state_dir: Path
    app_file: Path
    python_exe: Path
    llama_cpu_exe: Path
    llama_vulkan_exe: Path
    chat_model: Path

    @property
    def data_dir(self) -> Path:
        return self.state_dir / "data"

    @property
    def models_dir(self) -> Path:
        return self.state_dir / "models"

    @property
    def bundled_models_dir(self) -> Path:
        return self.install_dir / "models"

    @property
    def embedding_model_dir(self) -> Path:
        return self.bundled_models_dir / "huggingface" / "embedding"

    @property
    def reranker_model_dir(self) -> Path:
        return self.bundled_models_dir / "huggingface" / "reranker"

    @property
    def whisper_model_dir(self) -> Path:
        return self.bundled_models_dir / "huggingface" / "whisper-small"

    @property
    def easyocr_dir(self) -> Path:
        return self.bundled_models_dir / "easyocr"

    @property
    def soffice_exe(self) -> Path:
        return self.install_dir / "runtime" / "libreoffice" / "program" / "soffice.com"

    @property
    def logs_dir(self) -> Path:
        return self.state_dir / "logs"

    @classmethod
    def resolve(
        cls,
        install_dir: Path,
        state_dir: Path | None = None,
        python_exe: Path | None = None,
        llama_server_exe: Path | None = None,
        chat_model: Path | None = None,
    ) -> "DesktopLayout":
        install_dir = install_dir.resolve()
        if state_dir is None:
            local_app_data = os.getenv("LOCALAPPDATA")
            state_dir = (
                Path(local_app_data) / "Atlas"
                if local_app_data
                else Path.home() / "AppData" / "Local" / "Atlas"
            )
        state_dir = state_dir.resolve()
        packaged_app = install_dir / "app" / "app.py"
        app_file = packaged_app if packaged_app.exists() else install_dir / "app.py"
        state_model = state_dir / "models" / "chat.gguf"
        bundled_model = install_dir / "models" / "chat.gguf"
        default_model = state_model if state_model.exists() or not bundled_model.exists() else bundled_model
        branded_python = install_dir / "runtime" / "python" / "Atlas.exe"
        default_python = branded_python if branded_python.is_file() else install_dir / "runtime" / "python" / "python.exe"
        return cls(
            install_dir=install_dir,
            state_dir=state_dir,
            app_file=app_file,
            python_exe=(python_exe or default_python).resolve(),
            llama_cpu_exe=(
                llama_server_exe
                or install_dir / "runtime" / "llama" / "cpu" / "llama-server.exe"
            ).resolve(),
            llama_vulkan_exe=(
                install_dir / "runtime" / "llama" / "vulkan" / "llama-server.exe"
            ).resolve(),
            chat_model=(chat_model or default_model).resolve(),
        )

    @property
    def llama_server_exe(self) -> Path:
        """Compatibility alias for code that explicitly requests CPU mode."""
        return self.llama_cpu_exe

    def required_files(self) -> dict[str, Path]:
        return {
            "application": self.app_file,
            "python_runtime": self.python_exe,
            "llama_server_cpu": self.llama_cpu_exe,
            "chat_model": self.chat_model,
            "embedding_model": self.embedding_model_dir / "model.safetensors",
            "reranker_model": self.reranker_model_dir / "model.safetensors",
            "whisper_model": self.whisper_model_dir / "model.bin",
            "easyocr_detector": self.easyocr_dir / "model" / "craft_mlt_25k.pth",
            "easyocr_recognizer": self.easyocr_dir / "model" / "cyrillic_g2.pth",
            "libreoffice": self.soffice_exe,
        }

    def optional_files(self) -> dict[str, Path]:
        return {"llama_server_vulkan": self.llama_vulkan_exe}

    def missing_files(self) -> dict[str, Path]:
        return {
            name: path
            for name, path in self.required_files().items()
            if not path.is_file()
        }

    def ensure_state_directories(self) -> None:
        for path in (self.data_dir, self.models_dir, self.logs_dir):
            path.mkdir(parents=True, exist_ok=True)


def free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((LOOPBACK, 0))
        return int(sock.getsockname()[1])


def runtime_environment(
    layout: DesktopLayout, llama_port: int, api_key: str = ""
) -> dict[str, str]:
    environment = os.environ.copy()
    current_path = environment.get("PATH", "")
    environment.update(
        {
            "DATA_DIR": str(layout.data_dir),
            "LLM_BACKEND": "llama_cpp",
            "LLAMA_BASE_URL": f"http://{LOOPBACK}:{llama_port}",
            "LLAMA_API_KEY": api_key,
            "CHAT_MODEL": layout.chat_model.stem,
            "HF_HOME": str(layout.models_dir / "huggingface"),
            "EMBEDDING_MODEL": str(layout.embedding_model_dir),
            "RERANKER_MODEL": str(layout.reranker_model_dir),
            "WHISPER_MODEL": str(layout.whisper_model_dir),
            "EASYOCR_MODULE_PATH": str(layout.easyocr_dir),
            "EASYOCR_DOWNLOAD_ENABLED": "0",
            "ATLAS_SOFFICE_PATH": str(layout.soffice_exe),
            "ATLAS_LOW_MEMORY": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HF_HUB_OFFLINE": "1",
            "HF_DATASETS_OFFLINE": "1",
            "TOKENIZERS_PARALLELISM": "false",
            "PYTHONUTF8": "1",
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PATH": str(layout.soffice_exe.parent) + os.pathsep + current_path,
        }
    )
    return environment


def llama_command(
    layout: DesktopLayout,
    port: int,
    context_size: int,
    threads: int,
    backend: str = "cpu",
    api_key: str = "",
) -> list[str]:
    executable = layout.llama_vulkan_exe if backend == "vulkan" else layout.llama_cpu_exe
    command = [
        str(executable),
        "--model",
        str(layout.chat_model),
        "--host",
        LOOPBACK,
        "--port",
        str(port),
        "--ctx-size",
        str(context_size),
        "--parallel",
        "1",
        "--sleep-idle-seconds",
        "1",
        "--threads",
        str(max(1, threads)),
        "--n-gpu-layers",
        "all" if backend == "vulkan" else "0",
        "--no-webui",
    ]
    if api_key:
        command.extend(["--api-key", api_key])
    return command


def backend_candidates(layout: DesktopLayout, gpu_mode: str) -> list[str]:
    if gpu_mode == "off":
        return ["cpu"]
    if gpu_mode == "vulkan":
        return ["vulkan", "cpu"]
    return (["vulkan"] if layout.llama_vulkan_exe.is_file() else []) + ["cpu"]


def streamlit_command(layout: DesktopLayout, port: int) -> list[str]:
    return [
        str(layout.python_exe),
        "-m",
        "streamlit",
        "run",
        str(layout.app_file),
        f"--server.address={LOOPBACK}",
        f"--server.port={port}",
        "--server.headless=true",
        "--server.maxUploadSize=500",
        "--server.enableXsrfProtection=true",
        "--browser.gatherUsageStats=false",
    ]


def wait_for_health(
    url: str,
    process: subprocess.Popen,
    timeout: float,
    headers: dict[str, str] | None = None,
) -> None:
    deadline = time.monotonic() + timeout
    last_error = "сервис еще не готов"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Процесс завершился с кодом {process.returncode}: {url}")
        try:
            request = urllib.request.Request(url, headers=headers or {})
            with urllib.request.urlopen(request, timeout=2) as response:
                if 200 <= response.status < 300:
                    return
                last_error = f"HTTP {response.status}"
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = str(exc)
        time.sleep(0.25)
    raise TimeoutError(f"Сервис не запустился за {timeout:.0f} с: {url} ({last_error})")


def stop_process(process: subprocess.Popen | None, timeout: float = 8.0) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)


def check_payload(layout: DesktopLayout) -> dict:
    missing = layout.missing_files()
    return {
        "ready": not missing,
        "install_dir": str(layout.install_dir),
        "state_dir": str(layout.state_dir),
        "data_dir": str(layout.data_dir),
        "components": {
            name: {"path": str(path), "present": name not in missing}
            for name, path in layout.required_files().items()
        },
        "optional_components": {
            name: {"path": str(path), "present": path.is_file()}
            for name, path in layout.optional_files().items()
        },
        "offline_after_install": True,
        "bind_address": LOOPBACK,
    }


def runtime_state_path(layout: DesktopLayout) -> Path:
    return layout.state_dir / "runtime.json"


def write_runtime_state(layout: DesktopLayout, app_url: str, backend: str) -> None:
    payload = {
        "pid": os.getpid(),
        "app_url": app_url,
        "backend": backend,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    target = runtime_state_path(layout)
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(target)


def existing_app_url(layout: DesktopLayout) -> str | None:
    try:
        payload = json.loads(runtime_state_path(layout).read_text(encoding="utf-8"))
        url = str(payload.get("app_url") or "")
        parsed = urlparse(url)
        if (
            parsed.scheme == "http"
            and parsed.hostname == LOOPBACK
            and parsed.port is not None
            and parsed.username is None
            and parsed.password is None
        ):
            return url
    except (FileNotFoundError, json.JSONDecodeError, TypeError, ValueError):
        pass
    return None


def process_image_path(pid: int) -> Path | None:
    if os.name != "nt" or pid < 1:
        return None
    from ctypes import wintypes

    process_query_limited_information = 0x1000
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return None
    try:
        capacity = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(capacity.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(capacity)):
            return None
        return Path(buffer.value).resolve()
    finally:
        kernel32.CloseHandle(handle)


def _terminate_pid(pid: int) -> bool:
    if os.name != "nt":
        try:
            os.kill(pid, 15)
            return True
        except OSError:
            return False
    from ctypes import wintypes

    process_terminate = 0x0001
    synchronize = 0x00100000
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.OpenProcess(process_terminate | synchronize, False, pid)
    if not handle:
        return False
    try:
        if not kernel32.TerminateProcess(handle, 0):
            return False
        kernel32.WaitForSingleObject(handle, 8000)
        return True
    finally:
        kernel32.CloseHandle(handle)


def stop_running_atlas(layout: DesktopLayout) -> bool:
    try:
        payload = json.loads(runtime_state_path(layout).read_text(encoding="utf-8"))
        pid = int(payload["pid"])
    except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return False
    image = process_image_path(pid)
    allowed_names = {"atlas.exe", "python.exe", "pythonw.exe"}
    runtime_dir = (layout.install_dir / "runtime" / "python").resolve()
    if image is None or image.parent != runtime_dir or image.name.casefold() not in allowed_names:
        return False
    stopped = _terminate_pid(pid)
    if stopped:
        runtime_state_path(layout).unlink(missing_ok=True)
    return stopped


def physical_memory() -> dict[str, int] | None:
    if os.name != "nt":
        return None

    class MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("length", ctypes.c_ulong),
            ("memory_load", ctypes.c_ulong),
            ("total_physical", ctypes.c_ulonglong),
            ("available_physical", ctypes.c_ulonglong),
            ("total_page_file", ctypes.c_ulonglong),
            ("available_page_file", ctypes.c_ulonglong),
            ("total_virtual", ctypes.c_ulonglong),
            ("available_virtual", ctypes.c_ulonglong),
            ("available_extended_virtual", ctypes.c_ulonglong),
        ]

    status = MemoryStatus()
    status.length = ctypes.sizeof(status)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    if not kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        raise ctypes.WinError(ctypes.get_last_error())
    return {
        "total_physical": int(status.total_physical),
        "available_physical": int(status.available_physical),
        "memory_load_percent": int(status.memory_load),
    }


def vulkan_devices(layout: DesktopLayout) -> list[str]:
    if not layout.llama_vulkan_exe.is_file():
        return []
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        result = subprocess.run(
            [str(layout.llama_vulkan_exe), "--list-devices"],
            cwd=layout.llama_vulkan_exe.parent,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
            creationflags=creation_flags,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    lines = (result.stdout + "\n" + result.stderr).splitlines()
    return [line.strip() for line in lines if line.strip().startswith("Vulkan")]


def diagnostics_payload(layout: DesktopLayout) -> dict:
    memory = physical_memory()
    disk = shutil.disk_usage(layout.install_dir)
    minimum_memory = 8 * 1024**3
    minimum_disk_free = 12 * 1024**3
    return {
        "ready": not layout.missing_files(),
        "system": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "cpu_threads": os.cpu_count(),
            "memory": memory,
            "install_disk": {
                "total": disk.total,
                "free": disk.free,
            },
            "vulkan_devices": vulkan_devices(layout),
        },
        "minimums": {
            "memory_bytes": minimum_memory,
            "disk_free_bytes": minimum_disk_free,
            "memory_met": memory is None or memory["total_physical"] >= minimum_memory,
            "disk_met": disk.free >= minimum_disk_free,
        },
        "components": check_payload(layout),
    }


def run(
    layout: DesktopLayout, no_browser: bool, context_size: int, gpu_mode: str
) -> int:
    missing = layout.missing_files()
    if missing:
        details = "\n".join(f"- {name}: {path}" for name, path in missing.items())
        raise FileNotFoundError(f"Установочный комплект Atlas неполон:\n{details}")

    layout.ensure_state_directories()
    single_instance = WindowsSingleInstance(layout.state_dir)
    if not single_instance.acquired:
        url = existing_app_url(layout)
        if url and not no_browser:
            webbrowser.open(url)
        print("Atlas уже запущен." + (f" {url}" if url else ""))
        return 0
    runtime_state_path(layout).unlink(missing_ok=True)
    llama_port = free_local_port()
    app_port = free_local_port()
    threads = max(1, (os.cpu_count() or 2) - 1)
    api_key = secrets.token_urlsafe(32)
    environment = runtime_environment(layout, llama_port, api_key)
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    process_job = WindowsProcessJob()
    llama_process = None
    app_process = None
    llama_log = layout.logs_dir / "llama-server.log"
    app_log = layout.logs_dir / "atlas-backend.log"
    rotate_log(llama_log)
    rotate_log(app_log)
    active_backend = ""

    try:
        with llama_log.open("a", encoding="utf-8") as llama_output, app_log.open(
            "a", encoding="utf-8"
        ) as app_output:
            attempts = backend_candidates(layout, gpu_mode)
            last_error: Exception | None = None
            for backend in attempts:
                executable = (
                    layout.llama_vulkan_exe if backend == "vulkan" else layout.llama_cpu_exe
                )
                if not executable.is_file():
                    last_error = FileNotFoundError(executable)
                    continue
                llama_output.write(f"\nAtlas launcher: запуск backend={backend}\n")
                llama_output.flush()
                llama_process = subprocess.Popen(
                    llama_command(
                        layout, llama_port, context_size, threads, backend, api_key
                    ),
                    cwd=executable.parent,
                    env=environment,
                    stdout=llama_output,
                    stderr=subprocess.STDOUT,
                    creationflags=creation_flags,
                )
                process_job.assign(llama_process)
                try:
                    wait_for_health(
                        f"http://{LOOPBACK}:{llama_port}/health",
                        llama_process,
                        timeout=120 if backend == "cpu" else 45,
                        headers={"Authorization": f"Bearer {api_key}"},
                    )
                    active_backend = backend
                    break
                except (RuntimeError, TimeoutError) as exc:
                    last_error = exc
                    stop_process(llama_process)
                    llama_process = None
                    llama_output.write(f"Atlas launcher: backend={backend} недоступен: {exc}\n")
                    llama_output.flush()
            else:
                raise RuntimeError(f"Не удалось запустить llama-server: {last_error}")
            app_process = subprocess.Popen(
                streamlit_command(layout, app_port),
                cwd=layout.app_file.parent,
                env=environment,
                stdout=app_output,
                stderr=subprocess.STDOUT,
                creationflags=creation_flags,
            )
            process_job.assign(app_process)
            app_url = f"http://{LOOPBACK}:{app_port}"
            wait_for_health(
                f"{app_url}/_stcore/health", app_process, timeout=120
            )
            write_runtime_state(layout, app_url, active_backend)
            if not no_browser:
                webbrowser.open(app_url)
            while llama_process.poll() is None and app_process.poll() is None:
                time.sleep(0.5)
            failed = app_process if app_process.poll() is not None else llama_process
            raise RuntimeError(f"Компонент Atlas завершился с кодом {failed.returncode}")
    except KeyboardInterrupt:
        return 0
    finally:
        stop_process(app_process)
        stop_process(llama_process)
        process_job.close()
        runtime_state_path(layout).unlink(missing_ok=True)
        single_instance.close()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Локальный Windows-супервизор Atlas")
    parser.add_argument("--install-dir", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--python", dest="python_exe", type=Path)
    parser.add_argument("--llama-server", dest="llama_server_exe", type=Path)
    parser.add_argument("--model", dest="chat_model", type=Path)
    parser.add_argument("--context-size", type=int, default=8192)
    parser.add_argument("--gpu", choices=("auto", "off", "vulkan"), default="auto")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--diagnostics", action="store_true")
    parser.add_argument("--stop", action="store_true")
    parser.add_argument("--no-browser", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    layout = DesktopLayout.resolve(
        args.install_dir,
        args.state_dir,
        args.python_exe,
        args.llama_server_exe,
        args.chat_model,
    )
    if args.check:
        payload = check_payload(layout)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload["ready"] else 2
    if args.diagnostics:
        payload = diagnostics_payload(layout)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload["ready"] else 2
    if args.stop:
        if stop_running_atlas(layout):
            print("Atlas остановлен.")
        else:
            print("Atlas не запущен.")
        return 0
    try:
        return run(layout, args.no_browser, args.context_size, args.gpu)
    except (FileNotFoundError, RuntimeError, TimeoutError) as exc:
        print(f"Atlas не запущен: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
