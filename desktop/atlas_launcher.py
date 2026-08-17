from __future__ import annotations

import argparse
import ctypes
import json
import os
import secrets
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from dataclasses import dataclass
from pathlib import Path


LOOPBACK = "127.0.0.1"
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000


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
        return cls(
            install_dir=install_dir,
            state_dir=state_dir,
            app_file=app_file,
            python_exe=(python_exe or install_dir / "runtime" / "python" / "python.exe").resolve(),
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


def run(
    layout: DesktopLayout, no_browser: bool, context_size: int, gpu_mode: str
) -> int:
    missing = layout.missing_files()
    if missing:
        details = "\n".join(f"- {name}: {path}" for name, path in missing.items())
        raise FileNotFoundError(f"Установочный комплект Atlas неполон:\n{details}")

    layout.ensure_state_directories()
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
    try:
        return run(layout, args.no_browser, args.context_size, args.gpu)
    except (FileNotFoundError, RuntimeError, TimeoutError) as exc:
        print(f"Atlas не запущен: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
