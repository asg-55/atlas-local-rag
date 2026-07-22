"""Quick local check of the disk-backed lexical index at a target corpus size."""

from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rag_assistant.database import Database


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--chunks", type=int, default=50_000)
    args = parser.parse_args()
    if args.chunks < 1:
        raise SystemExit("--chunks must be positive")

    with tempfile.TemporaryDirectory() as folder:
        db = Database(Path(folder) / "scale.db")
        if not db.fts_available():
            raise SystemExit("SQLite FTS5 is unavailable")
        document_id = db.create_document("scale.txt", "scale", ".txt", 0, "scale.txt")
        rows = [
            {
                "content": (
                    f"Технический фрагмент номер {index}. Давление и температура установки."
                    + (" Контрольный маркер atlas-scale-needle." if index == args.chunks - 1 else "")
                ),
                "content_hash": f"hash-{index}",
                "location": f"стр. {index + 1}",
                "chunk_index": index,
            }
            for index in range(args.chunks)
        ]
        started = time.perf_counter()
        ids = db.add_chunks(document_id, "scale.txt", rows)
        db.finish_document(document_id, len(ids))
        indexed_seconds = time.perf_counter() - started

        started = time.perf_counter()
        results = db.lexical_search_fts(["atlas", "scale", "needle"], 10)
        search_ms = (time.perf_counter() - started) * 1000
        found = bool(results and results[0][0] == ids[-1])
        print(
            f"chunks={len(ids)} index_seconds={indexed_seconds:.3f} "
            f"search_ms={search_ms:.3f} target_found={found}"
        )
        if not found:
            raise SystemExit("Target chunk was not ranked first")


if __name__ == "__main__":
    main()
