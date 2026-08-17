from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
ISS = ROOT / "desktop" / "installer" / "atlas-desktop.iss"
BUILDER = ROOT / "desktop" / "build_installer.ps1"


class DesktopInstallerTests(unittest.TestCase):
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
        self.assertIn("[switch]$ValidateOnly", script)


if __name__ == "__main__":
    unittest.main()
