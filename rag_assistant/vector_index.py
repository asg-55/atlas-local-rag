from __future__ import annotations

import json
from pathlib import Path

import faiss
import numpy as np

from .database import Database


class VectorIndex:
    def __init__(self, db: Database, index_path: Path, meta_path: Path, model_name: str):
        self.db = db
        self.index_path = index_path
        self.meta_path = meta_path
        self.model_name = model_name
        self.index = None
        self.chunk_ids: list[int] = []
        self.loaded_generation = -1

    def ensure_loaded(self) -> None:
        generation = self.db.generation()
        if self.index is not None and self.loaded_generation == generation:
            return
        if self.index_path.exists() and self.meta_path.exists():
            try:
                meta = json.loads(self.meta_path.read_text(encoding="utf-8"))
                if meta.get("model") == self.model_name:
                    self.index = faiss.read_index(str(self.index_path))
                    self.chunk_ids = [int(value) for value in meta["chunk_ids"]]
                    self.loaded_generation = int(meta.get("generation", -1))
                    if self.index.ntotal != len(self.chunk_ids):
                        raise ValueError("Vector index metadata does not match the index")
                    if self.loaded_generation == generation:
                        return
            except (OSError, ValueError, KeyError, RuntimeError):
                self.index = None
                self.chunk_ids = []
                self.loaded_generation = -1
        self.sync()

    def _persist(self, generation: int, dimension: int) -> None:
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        faiss.write_index(self.index, str(self.index_path))
        self.meta_path.write_text(
            json.dumps(
                {
                    "generation": generation,
                    "model": self.model_name,
                    "dimension": dimension,
                    "chunk_ids": self.chunk_ids,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def sync(self) -> None:
        """Append newly embedded chunks; rebuild only when old chunks disappeared."""
        generation = self.db.generation()
        if self.index is None or not self.chunk_ids:
            self.rebuild()
            return
        last_id = self.chunk_ids[-1]
        if self.db.embedded_count_through(last_id) != len(self.chunk_ids):
            self.rebuild()
            return
        rows = self.db.embedded_chunks_after(last_id)
        if rows:
            dimension = int(self.index.d)
            vectors = np.vstack(
                [np.frombuffer(row["embedding"], dtype="float32", count=dimension) for row in rows]
            ).astype("float32")
            if vectors.shape[1] != dimension:
                self.rebuild()
                return
            faiss.normalize_L2(vectors)
            self.index.add(vectors)
            self.chunk_ids.extend(int(row["id"]) for row in rows)
        self._persist(generation, int(self.index.d))
        self.loaded_generation = generation

    def rebuild(self) -> None:
        rows = self.db.all_chunks(only_embedded=True)
        generation = self.db.generation()
        if not rows:
            self.index = None
            self.chunk_ids = []
            self.loaded_generation = generation
            self.index_path.unlink(missing_ok=True)
            self.meta_path.unlink(missing_ok=True)
            return
        dimension = len(rows[0]["embedding"]) // 4
        vectors = np.vstack(
            [np.frombuffer(row["embedding"], dtype="float32", count=dimension) for row in rows]
        ).astype("float32")
        faiss.normalize_L2(vectors)
        index = faiss.IndexFlatIP(dimension)
        index.add(vectors)
        chunk_ids = [int(row["id"]) for row in rows]
        self.index = index
        self.chunk_ids = chunk_ids
        self._persist(generation, dimension)
        self.loaded_generation = generation

    def search(self, query_vector: np.ndarray, k: int) -> list[tuple[int, float]]:
        self.ensure_loaded()
        if self.index is None or not self.chunk_ids:
            return []
        vector = np.asarray(query_vector, dtype="float32").reshape(1, -1)
        faiss.normalize_L2(vector)
        scores, positions = self.index.search(vector, min(k, len(self.chunk_ids)))
        return [
            (self.chunk_ids[int(position)], float(score))
            for position, score in zip(positions[0], scores[0])
            if position >= 0
        ]
