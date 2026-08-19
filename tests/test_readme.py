import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
DOCKERFILE = ROOT / "Dockerfile"
ILLUSTRATIONS = (
    ROOT / "docs" / "assets" / "atlas-readme-hero.svg",
    ROOT / "docs" / "assets" / "atlas-architecture.svg",
    ROOT / "docs" / "assets" / "atlas-local-data-flow.svg",
)


class ReadmeTests(unittest.TestCase):
    def test_ci_image_contains_readme_assets_but_runtime_layer_does_not(self):
        content = DOCKERFILE.read_text(encoding="utf-8")
        test_stage = content.split("FROM atlas-runtime-base AS test", 1)[1].split(
            "FROM atlas-runtime-base AS runtime", 1
        )[0]
        self.assertIn("COPY README.md ./", test_stage)
        self.assertIn("COPY docs/assets ./docs/assets", test_stage)

    def test_readme_references_all_atlas_illustrations(self):
        content = README.read_text(encoding="utf-8")
        for illustration in ILLUSTRATIONS:
            relative = illustration.relative_to(ROOT).as_posix()
            self.assertIn(f'src="{relative}"', content)

    def test_atlas_illustrations_are_valid_accessible_svg(self):
        for illustration in ILLUSTRATIONS:
            root = ET.parse(illustration).getroot()
            self.assertEqual("{http://www.w3.org/2000/svg}svg", root.tag)
            self.assertEqual("1400", root.attrib["width"])
            self.assertIsNotNone(root.find("{http://www.w3.org/2000/svg}title"))
            self.assertIsNotNone(root.find("{http://www.w3.org/2000/svg}desc"))


if __name__ == "__main__":
    unittest.main()
