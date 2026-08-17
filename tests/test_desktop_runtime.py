import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from desktop.prepare_runtime import (
    DEFAULT_MANIFEST,
    install_component,
    load_manifest,
    safe_extract,
    verify_download,
)
from desktop.generate_windows_lock import build_lock
from desktop.prepare_offline_models import prepare_models, safe_target


class DesktopRuntimeTests(unittest.TestCase):
    def test_checked_in_manifest_pins_desktop_model_and_backends(self):
        manifest = load_manifest(DEFAULT_MANIFEST)
        components = {item["id"]: item for item in manifest["components"]}

        self.assertEqual("qwen35_4b_q4km", manifest["defaults"]["model_component"])
        self.assertEqual(8192, manifest["defaults"]["context_size"])
        self.assertEqual(
            {
                "python_embed",
                "pip_bootstrap",
                "libreoffice_windows",
                "easyocr_detector",
                "easyocr_cyrillic",
                "llama_cpu",
                "llama_vulkan",
                "qwen35_4b_q4km",
            },
            set(components),
        )
        self.assertEqual("models/chat.gguf", components["qwen35_4b_q4km"]["destination"])
        self.assertEqual("Apache-2.0", components["qwen35_4b_q4km"]["license"])
        self.assertEqual("3.11.9", components["python_embed"]["version"])
        self.assertTrue(components["pip_bootstrap"]["build_only"])
        self.assertEqual("f15ba07bfcb0186986cf3171063506f5d207c11f8cc051ba0d135209e9e915f9", components["libreoffice_windows"]["sha256"])
        self.assertEqual("2f8227d2def4037cdb3b34389dcf9ec1", components["easyocr_detector"]["extracted_md5"])

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

    def test_windows_lock_contains_only_unique_hashed_wheels(self):
        lock_path = DEFAULT_MANIFEST.with_name("requirements-windows.lock.json")
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        packages = lock["packages"]
        names = [item["name"].lower().replace("_", "-") for item in packages]

        self.assertTrue(lock["only_binary"])
        self.assertGreaterEqual(len(packages), 80)
        self.assertEqual(len(names), len(set(names)))
        self.assertTrue(all(item["filename"].endswith(".whl") for item in packages))
        self.assertTrue(all(len(item["sha256"]) == 64 for item in packages))
        self.assertIn("torch", names)
        self.assertIn("faiss-cpu", names)

    def test_lock_generator_applies_a_hashed_source_override(self):
        report = {
            "install": [
                {
                    "metadata": {"name": "Example", "version": "1.0"},
                    "download_info": {
                        "url": "https://mirror.invalid/example-1.0.whl",
                        "archive_info": {},
                    },
                    "requested": False,
                }
            ]
        }
        overrides = {
            "Example": {
                "filename": "example-1.0-py3-none-any.whl",
                "url": "https://example.invalid/example.whl",
                "sha256": "a" * 64,
            }
        }
        lock = build_lock(report, overrides)

        self.assertEqual("a" * 64, lock["packages"][0]["sha256"])
        self.assertEqual("example-1.0-py3-none-any.whl", lock["packages"][0]["filename"])

    def test_windows_builder_installs_from_the_verified_offline_wheelhouse(self):
        builder = DEFAULT_MANIFEST.with_name("build_python_runtime.ps1").read_text(
            encoding="utf-8"
        )

        self.assertIn("requirements-windows.lock.json", builder)
        self.assertIn("--only-binary=:all:", builder)
        self.assertIn("--no-index", builder)
        self.assertIn("Get-FileHash -Algorithm SHA256", builder)
        self.assertIn("pip_in_runtime", builder)

    def test_model_pack_manifest_pins_revisions_and_only_required_files(self):
        path = DEFAULT_MANIFEST.with_name("model-packs.json")
        manifest = json.loads(path.read_text(encoding="utf-8"))
        models = {item["id"]: item for item in manifest["models"]}

        self.assertEqual({"embedding", "reranker", "whisper-small"}, set(models))
        self.assertTrue(all(len(item["revision"]) == 40 for item in models.values()))
        self.assertTrue(all("*" not in pattern for item in models.values() for pattern in item["allow_patterns"]))
        self.assertIn("model.safetensors", models["embedding"]["allow_patterns"])
        self.assertNotIn("pytorch_model.bin", models["embedding"]["allow_patterns"])

    def test_offline_asset_builder_uses_verified_archives_and_msi_extraction(self):
        builder = DEFAULT_MANIFEST.with_name("build_offline_assets.ps1").read_text(
            encoding="utf-8"
        )

        self.assertIn("Get-FileHash -Algorithm SHA256", builder)
        self.assertIn("Get-FileHash -Algorithm MD5", builder)
        self.assertIn("msiexec.exe /a", builder)
        self.assertIn("prepare_offline_models.py", builder)
        self.assertIn("ffmpeg_cli_required = $false", builder)
        self.assertIn("--continue-at -", builder)

    def test_model_pack_preparation_copies_only_declared_snapshot_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = root / "snapshot"
            snapshot.mkdir()
            (snapshot / "model.safetensors").write_bytes(b"weights")
            manifest = root / "models.json"
            manifest.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "models": [
                            {
                                "id": "embedding",
                                "repository": "example/model",
                                "revision": "a" * 40,
                                "destination": "embedding",
                                "allow_patterns": ["model.safetensors"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with patch("desktop.prepare_offline_models.download_snapshot", return_value=snapshot):
                result = prepare_models(manifest, root / "output", root / "cache")

            installed = root / "output" / "embedding" / "model.safetensors"
            self.assertEqual(b"weights", installed.read_bytes())
            self.assertEqual(7, result["models"][0]["bytes"])

    def test_model_pack_destination_cannot_escape_staging(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                safe_target(Path(directory), "../outside")


if __name__ == "__main__":
    unittest.main()
