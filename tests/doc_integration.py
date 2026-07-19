"""Manual legacy .doc conversion smoke test used in Docker verification."""

import subprocess
import tempfile
from pathlib import Path

from rag_assistant.parsers import parse_file


def main() -> None:
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        rtf_path = root / "legacy-source.rtf"
        rtf = r"""{\rtf1\ansi\ansicpg1251\deff0
{\fonttbl{\f0 Times New Roman;}}
\f0\fs28 Регламент оборудования\par
\fs24 Рабочее давление составляет 5 МПа.\par
\trowd\cellx5000\cellx9000 Параметр\cell Значение\cell\row
\trowd\cellx5000\cellx9000 Период ТО\cell 500 часов\cell\row
}"""
        rtf_path.write_bytes(rtf.encode("cp1251"))
        result = subprocess.run(
            ["soffice", "--headless", "--convert-to", "doc", "--outdir", str(root), str(rtf_path)],
            capture_output=True,
            text=True,
            timeout=180,
        )
        doc_path = root / "legacy-source.doc"
        assert result.returncode == 0 and doc_path.exists(), result.stderr or result.stdout
        blocks = parse_file(doc_path)
        text = "\n".join(block.text for block in blocks)
        print(text)
        print("block_types", [block.block_type for block in blocks])
        assert "5 МПа" in text, text
        assert "500 часов" in text, text
        assert any(block.block_type == "table" for block in blocks), blocks
        assert "Параметр | Значение" in text, text


if __name__ == "__main__":
    main()
