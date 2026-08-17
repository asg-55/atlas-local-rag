from __future__ import annotations

import importlib
import json
import platform
import sqlite3
import sys
from pathlib import Path


REQUIRED_MODULES = (
    "streamlit",
    "torch",
    "torchvision",
    "faiss",
    "sentence_transformers",
    "rank_bm25",
    "fitz",
    "docx",
    "pandas",
    "openpyxl",
    "easyocr",
    "faster_whisper",
    "PIL",
    "requests",
    "numpy",
    "lxml",
    "cryptography",
    "cv2",
)


def module_version(module) -> str:
    return str(getattr(module, "__version__", "bundled"))


def validate_fts5() -> None:
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("CREATE VIRTUAL TABLE probe USING fts5(body)")
        connection.execute("INSERT INTO probe(body) VALUES (?)", ("Atlas работает",))
        row = connection.execute(
            "SELECT body FROM probe WHERE probe MATCH ?", ("Atlas",)
        ).fetchone()
        if row != ("Atlas работает",):
            raise RuntimeError("SQLite FTS5 не вернул контрольную строку")
    finally:
        connection.close()


def validate_faiss(faiss, numpy) -> None:
    index = faiss.IndexFlatIP(3)
    vectors = numpy.asarray([[1.0, 0.0, 0.0]], dtype="float32")
    index.add(vectors)
    _, positions = index.search(vectors, 1)
    if int(positions[0][0]) != 0:
        raise RuntimeError("FAISS не нашёл контрольный вектор")


def validate_runtime() -> dict:
    modules = {name: importlib.import_module(name) for name in REQUIRED_MODULES}
    validate_fts5()
    validate_faiss(modules["faiss"], modules["numpy"])
    torch = modules["torch"]
    return {
        "ready": True,
        "python": platform.python_version(),
        "architecture": platform.machine(),
        "executable": str(Path(sys.executable).resolve()),
        "sqlite": sqlite3.sqlite_version,
        "fts5": True,
        "faiss": True,
        "torch_cuda_available": bool(torch.cuda.is_available()),
        "modules": {name: module_version(module) for name, module in modules.items()},
    }


def main() -> int:
    try:
        result = validate_runtime()
    except Exception as exc:
        print(json.dumps({"ready": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
