from pathlib import Path
import tempfile
import unittest

from desktop.payload_manifest import create_manifest, verify_manifest

ROOT = Path(__file__).resolve().parents[1]
ISS = ROOT / "desktop" / "installer" / "atlas-desktop.iss"
BUILDER = ROOT / "desktop" / "build_installer.ps1"
PREPARER = ROOT / "desktop" / "prepare_installer_payload.ps1"


class DesktopInstallerTests(unittest.TestCase):
    def test_payload_manifest_detects_content_changes_and_extra_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app").mkdir()
            sample = root / "app" / "app.py"
            sample.write_text("print('atlas')", encoding="utf-8")
            manifest = root / "payload-manifest.json"
            payload = create_manifest(root, manifest, "a" * 40)

            self.assertEqual(1, payload["file_count"])
            self.assertEqual(payload, verify_manifest(root, manifest))
            sample.write_text("changed", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Размер|SHA-256"):
                verify_manifest(root, manifest)

    def test_payload_manifest_rejects_forbidden_cache_and_links(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "downloads").mkdir()
            (root / "downloads" / "cache.bin").write_bytes(b"cache")
            with self.assertRaisesRegex(ValueError, "Запрещённый каталог"):
                create_manifest(root, root / "payload-manifest.json", "a" * 40)

    def test_installer_is_per_user_and_keeps_state_outside_app_directory(self):
        script = ISS.read_text(encoding="utf-8")
        self.assertIn("PrivilegesRequired=lowest", script)
        self.assertIn(r"DefaultDirName={localappdata}\Programs\Atlas", script)
        self.assertNotIn(r"{localappdata}\Atlas\data", script)
        self.assertNotIn("[UninstallDelete]", script)

    def test_large_distribution_uses_bounded_slices(self):
        script = ISS.read_text(encoding="utf-8")
        self.assertIn("DiskSpanning=yes", script)
        self.assertIn("DiskSliceSize=2000000000", script)
        self.assertIn("SolidCompression=no", script)

    def test_installer_launches_bundled_runtime_without_docker_or_ollama(self):
        script = ISS.read_text(encoding="utf-8")
        self.assertIn(r"runtime\python\pythonw.exe", script)
        self.assertIn("atlas_launcher.py", script)
        self.assertNotIn("docker", script.casefold())
        self.assertNotIn("ollama", script.casefold())

    def test_builder_validates_payload_and_emits_checksums(self):
        script = BUILDER.read_text(encoding="utf-8")
        self.assertIn("--check --install-dir", script)
        self.assertIn("Get-FileHash -Algorithm SHA256", script)
        self.assertIn("SHA256SUMS.txt", script)
        self.assertIn("payload_manifest.py", script)
        self.assertIn("Inno Setup 7 Command-Line Compiler", script)
        self.assertIn("[switch]$ValidateOnly", script)

    def test_preparer_uses_only_built_runtime_models_and_current_source(self):
        script = PREPARER.read_text(encoding="utf-8")
        self.assertIn("[Parameter(Mandatory = $true)]", script)
        self.assertIn('@("runtime", "models")', script)
        self.assertIn('Join-Path $projectRoot "rag_assistant"', script)
        self.assertIn("status --porcelain", script)
        self.assertNotIn('Join-Path $projectRoot "data"', script)
        self.assertNotIn('Join-Path $projectRoot ".env"', script)


if __name__ == "__main__":
    unittest.main()
