import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from desktop.atlas_launcher import (
    DesktopLayout,
    LOOPBACK,
    WindowsSingleInstance,
    WindowsProcessJob,
    backend_candidates,
    check_payload,
    diagnostics_payload,
    existing_app_url,
    instance_name,
    llama_command,
    rotate_log,
    runtime_environment,
    streamlit_command,
    write_runtime_state,
)


class DesktopLauncherTests(unittest.TestCase):
    def test_instance_name_is_stable_and_scoped_to_state_directory(self):
        first = instance_name(Path("state-a"))
        self.assertEqual(first, instance_name(Path("state-a")))
        self.assertNotEqual(first, instance_name(Path("state-b")))
        self.assertTrue(first.startswith("Local\\AtlasDesktop-"))

    def test_native_mutex_rejects_second_instance_for_same_state(self):
        if os.name != "nt":
            self.skipTest("Windows mutex behavior only")
        with tempfile.TemporaryDirectory() as directory:
            first = WindowsSingleInstance(Path(directory))
            second = WindowsSingleInstance(Path(directory))
            try:
                self.assertTrue(first.acquired)
                self.assertFalse(second.acquired)
            finally:
                second.close()
                first.close()

    def test_log_rotation_keeps_bounded_backups(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "atlas.log"
            log.write_text("first", encoding="utf-8")
            rotate_log(log, max_bytes=1, backups=2)
            log.write_text("second", encoding="utf-8")
            rotate_log(log, max_bytes=1, backups=2)
            log.write_text("third", encoding="utf-8")
            rotate_log(log, max_bytes=1, backups=2)

            self.assertEqual("third", (log.with_name("atlas.log.1")).read_text())
            self.assertEqual("second", (log.with_name("atlas.log.2")).read_text())
            self.assertFalse(log.with_name("atlas.log.3").exists())

    def test_runtime_state_accepts_only_loopback_url(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            layout = DesktopLayout.resolve(root, root / "state")
            layout.state_dir.mkdir()
            write_runtime_state(layout, "http://127.0.0.1:19001", "cpu")
            self.assertEqual("http://127.0.0.1:19001", existing_app_url(layout))

            runtime = layout.state_dir / "runtime.json"
            runtime.write_text('{"app_url":"https://example.com"}', encoding="utf-8")
            self.assertIsNone(existing_app_url(layout))

    def test_process_job_is_a_safe_noop_outside_windows(self):
        if os.name == "nt":
            self.skipTest("Non-Windows behavior only")
        job = WindowsProcessJob()
        self.assertFalse(job.enabled)
        job.assign(object())
        job.close()

    def test_layout_separates_application_and_user_data(self):
        with tempfile.TemporaryDirectory() as install, tempfile.TemporaryDirectory() as local:
            install_dir = Path(install)
            (install_dir / "app.py").touch()
            with patch.dict(os.environ, {"LOCALAPPDATA": local}):
                layout = DesktopLayout.resolve(install_dir)

            self.assertEqual(install_dir.resolve(), layout.install_dir)
            self.assertEqual((Path(local) / "Atlas").resolve(), layout.state_dir)
            self.assertEqual(layout.state_dir / "data", layout.data_dir)
            self.assertNotEqual(layout.install_dir, layout.state_dir)

    def test_portable_bundle_model_is_used_without_copying_to_user_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundled = root / "models" / "chat.gguf"
            bundled.parent.mkdir()
            bundled.touch()
            layout = DesktopLayout.resolve(root, root / "state")

            self.assertEqual(bundled.resolve(), layout.chat_model)
            self.assertFalse((layout.state_dir / "models" / "chat.gguf").exists())

    def test_runtime_is_offline_and_uses_llama_cpp(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            layout = DesktopLayout.resolve(root, root / "state")
            environment = runtime_environment(layout, 19001)

            self.assertEqual("llama_cpp", environment["LLM_BACKEND"])
            self.assertEqual(f"http://{LOOPBACK}:19001", environment["LLAMA_BASE_URL"])
            self.assertEqual("1", environment["TRANSFORMERS_OFFLINE"])
            self.assertEqual("1", environment["HF_HUB_OFFLINE"])
            self.assertEqual(str(layout.data_dir), environment["DATA_DIR"])
            self.assertEqual("1", environment["PYTHONNOUSERSITE"])
            self.assertEqual("1", environment["PYTHONDONTWRITEBYTECODE"])
            self.assertEqual("1", environment["ATLAS_LOW_MEMORY"])
            self.assertEqual("0", environment["EASYOCR_DOWNLOAD_ENABLED"])
            self.assertEqual(str(layout.embedding_model_dir), environment["EMBEDDING_MODEL"])
            self.assertEqual(str(layout.reranker_model_dir), environment["RERANKER_MODEL"])
            self.assertEqual(str(layout.whisper_model_dir), environment["WHISPER_MODEL"])
            self.assertEqual(str(layout.soffice_exe), environment["ATLAS_SOFFICE_PATH"])

    def test_commands_bind_only_to_loopback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            layout = DesktopLayout.resolve(root, root / "state")

            llama = llama_command(layout, 19001, 8192, 6, "cpu", "secret")
            streamlit = streamlit_command(layout, 19002)

            self.assertEqual(LOOPBACK, llama[llama.index("--host") + 1])
            self.assertIn(f"--server.address={LOOPBACK}", streamlit)
            self.assertNotIn("0.0.0.0", llama + streamlit)
            self.assertEqual("0", llama[llama.index("--n-gpu-layers") + 1])
            self.assertEqual("1", llama[llama.index("--parallel") + 1])
            self.assertEqual("1", llama[llama.index("--sleep-idle-seconds") + 1])
            self.assertEqual("secret", llama[llama.index("--api-key") + 1])
            self.assertIn("--no-webui", llama)

    def test_vulkan_is_optional_and_cpu_is_always_the_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            layout = DesktopLayout.resolve(root, root / "state")
            self.assertEqual(["cpu"], backend_candidates(layout, "auto"))

            layout.llama_vulkan_exe.parent.mkdir(parents=True)
            layout.llama_vulkan_exe.touch()
            self.assertEqual(["vulkan", "cpu"], backend_candidates(layout, "auto"))
            self.assertEqual(["cpu"], backend_candidates(layout, "off"))

            command = llama_command(layout, 19001, 8192, 6, "vulkan")
            self.assertEqual("all", command[command.index("--n-gpu-layers") + 1])
            self.assertEqual(str(layout.llama_vulkan_exe), command[0])

    def test_check_reports_each_missing_component_without_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            layout = DesktopLayout.resolve(root, root / "state")
            payload = check_payload(layout)

            self.assertFalse(payload["ready"])
            self.assertFalse(layout.state_dir.exists())
            self.assertEqual(
                {
                    "application",
                    "python_runtime",
                    "llama_server_cpu",
                    "chat_model",
                    "embedding_model",
                    "reranker_model",
                    "whisper_model",
                    "easyocr_detector",
                    "easyocr_recognizer",
                    "libreoffice",
                },
                set(payload["components"]),
            )
            self.assertIn("llama_server_vulkan", payload["optional_components"])

    def test_diagnostics_reports_minimums_without_mutating_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            layout = DesktopLayout.resolve(root, root / "state")
            with patch(
                "desktop.atlas_launcher.physical_memory",
                return_value={
                    "total_physical": 16 * 1024**3,
                    "available_physical": 8 * 1024**3,
                    "memory_load_percent": 50,
                },
            ), patch("desktop.atlas_launcher.vulkan_devices", return_value=["Vulkan0: GPU"]):
                payload = diagnostics_payload(layout)

            self.assertTrue(payload["minimums"]["memory_met"])
            self.assertEqual(["Vulkan0: GPU"], payload["system"]["vulkan_devices"])
            self.assertFalse(layout.state_dir.exists())


if __name__ == "__main__":
    unittest.main()
