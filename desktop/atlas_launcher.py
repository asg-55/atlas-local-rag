from __future__ import annotations

import argparse
import json
import os
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


@dataclass(frozen=True)
class DesktopLayout:
    install_dir: Path
    state_dir: Path
    app_file: Path
    python_exe: Path
    llama_server_exe: Path
    chat_model: Path

    @property
    def data_dir(self) -> Path:
        return self.state_dir / "data"

    @property
    def models_dir(self) -> Path:
        return self.state_dir / "models"

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
        return cls(
            install_dir=install_dir,
            state_dir=state_dir,
            app_file=app_file,
            python_exe=(python_exe or install_dir / "runtime" / "python" / "python.exe").resolve(),
            llama_server_exe=(
                llama_server_exe
                or install_dir / "runtime" / "llama" / "llama-server.exe"
            ).resolve(),
            chat_model=(chat_model or state_dir / "models" / "chat.gguf").resolve(),
        )

    def required_files(self) -> dict[str, Path]:
        return {
            "application": self.app_file,
            "python_runtime": self.python_exe,
            "llama_server": self.llama_server_exe,
            "chat_model": self.chat_model,
        }

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


def runtime_environment(layout: DesktopLayout, llama_port: int) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "DATA_DIR": str(layout.data_dir),
            "LLM_BACKEND": "llama_cpp",
            "LLAMA_BASE_URL": f"http://{LOOPBACK}:{llama_port}",
            "CHAT_MODEL": layout.chat_model.stem,
            "HF_HOME": str(layout.models_dir / "huggingface"),
            "EASYOCR_MODULE_PATH": str(layout.models_dir / "easyocr"),
            "TRANSFORMERS_OFFLINE": "1",
            "HF_HUB_OFFLINE": "1",
            "HF_DATASETS_OFFLINE": "1",
            "TOKENIZERS_PARALLELISM": "false",
            "PYTHONUTF8": "1",
        }
    )
    return environment


def llama_command(
    layout: DesktopLayout,
    port: int,
    context_size: int,
    threads: int,
) -> list[str]:
    return [
        str(layout.llama_server_exe),
        "--model",
        str(layout.chat_model),
        "--host",
        LOOPBACK,
        "--port",
        str(port),
        "--ctx-size",
        str(context_size),
        "--threads",
        str(max(1, threads)),
    ]


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


def wait_for_health(url: str, process: subprocess.Popen, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    last_error = "сервис еще не готов"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Процесс завершился с кодом {process.returncode}: {url}")
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
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
        "offline_after_install": True,
        "bind_address": LOOPBACK,
    }


def run(layout: DesktopLayout, no_browser: bool, context_size: int) -> int:
    missing = layout.missing_files()
    if missing:
        details = "\n".join(f"- {name}: {path}" for name, path in missing.items())
        raise FileNotFoundError(f"Установочный комплект Atlas неполон:\n{details}")

    layout.ensure_state_directories()
    llama_port = free_local_port()
    app_port = free_local_port()
    threads = max(1, (os.cpu_count() or 2) - 1)
    environment = runtime_environment(layout, llama_port)
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    llama_process = None
    app_process = None
    llama_log = layout.logs_dir / "llama-server.log"
    app_log = layout.logs_dir / "atlas-backend.log"

    try:
        with llama_log.open("a", encoding="utf-8") as llama_output, app_log.open(
            "a", encoding="utf-8"
        ) as app_output:
            llama_process = subprocess.Popen(
                llama_command(layout, llama_port, context_size, threads),
                cwd=layout.install_dir,
                env=environment,
                stdout=llama_output,
                stderr=subprocess.STDOUT,
                creationflags=creation_flags,
            )
            wait_for_health(
                f"http://{LOOPBACK}:{llama_port}/health", llama_process, timeout=120
            )
            app_process = subprocess.Popen(
                streamlit_command(layout, app_port),
                cwd=layout.app_file.parent,
                env=environment,
                stdout=app_output,
                stderr=subprocess.STDOUT,
                creationflags=creation_flags,
            )
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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Локальный Windows-супервизор Atlas")
    parser.add_argument("--install-dir", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--python", dest="python_exe", type=Path)
    parser.add_argument("--llama-server", dest="llama_server_exe", type=Path)
    parser.add_argument("--model", dest="chat_model", type=Path)
    parser.add_argument("--context-size", type=int, default=16384)
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
        return run(layout, args.no_browser, args.context_size)
    except (FileNotFoundError, RuntimeError, TimeoutError) as exc:
        print(f"Atlas не запущен: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
