import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from desktop.prepare_runtime import (
    DEFAULT_MANIFEST,
    install_component,
    load_manifest,
    safe_extract,
    verify_download,
)


class DesktopRuntimeTests(unittest.TestCase):
    def test_checked_in_manifest_pins_desktop_model_and_backends(self):
        manifest = load_manifest(DEFAULT_MANIFEST)
        components = {item["id"]: item for item in manifest["components"]}

        self.assertEqual("qwen35_4b_q4km", manifest["defaults"]["model_component"])
        self.assertEqual(8192, manifest["defaults"]["context_size"])
        self.assertEqual(
            {"llama_cpu", "llama_vulkan", "qwen35_4b_q4km"}, set(components)
        )
        self.assertEqual("models/chat.gguf", components["qwen35_4b_q4km"]["destination"])
        self.assertEqual("Apache-2.0", components["qwen35_4b_q4km"]["license"])

    def test_verify_download_checks_size_and_sha256(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "component.bin"
            artifact.write_bytes(b"atlas")
            component = {
                "id": "sample",
                "size": 5,
                "sha256": hashlib.sha256(b"atlas").hexdigest(),
            }
            verify_download(artifact, component)
            artifact.write_bytes(b"broken")
            with self.assertRaises(ValueError):
                verify_download(artifact, component)

    def test_safe_extract_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "unsafe.zip"
            with zipfile.ZipFile(archive, "w") as package:
                package.writestr("../outside.txt", "no")
            with self.assertRaises(ValueError):
                safe_extract(archive, root / "runtime")
            self.assertFalse((root / "outside.txt").exists())

    def test_file_component_is_installed_inside_staging(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "source.gguf"
            artifact.write_bytes(b"model")
            component = {
                "kind": "file",
                "destination": "models/chat.gguf",
            }
            installed = install_component(component, artifact, root / "staging")
            self.assertEqual(b"model", installed.read_bytes())

    def test_manifest_rejects_duplicate_component_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            component = {
                "id": "same",
                "kind": "file",
                "filename": "x",
                "url": "https://example.invalid/x",
                "sha256": "0" * 64,
                "size": 1,
                "destination": "x",
            }
            path.write_text(
                json.dumps({"schema_version": 1, "components": [component, component]}),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                load_manifest(path)


if __name__ == "__main__":
    unittest.main()
