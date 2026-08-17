import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from desktop.atlas_launcher import (
    DesktopLayout,
    LOOPBACK,
    backend_candidates,
    check_payload,
    llama_command,
    runtime_environment,
    streamlit_command,
)


class DesktopLauncherTests(unittest.TestCase):
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
                {"application", "python_runtime", "llama_server_cpu", "chat_model"},
                set(payload["components"]),
            )
            self.assertIn("llama_server_vulkan", payload["optional_components"])


if __name__ == "__main__":
    unittest.main()
