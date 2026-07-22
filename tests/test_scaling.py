import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

from rag_assistant.config import Settings
from rag_assistant.database import Database
from rag_assistant.retrieval import HybridRetriever
from rag_assistant.vector_index import VectorIndex


def add_document(db: Database, name: str, digest: str, text: str, vector: list[float]):
    document_id = db.create_document(name, digest, ".txt", len(text), name)
    chunk_ids = db.add_chunks(
        document_id,
        name,
        [
            {
                "content": text,
                "content_hash": digest,
                "location": "стр. 1",
                "chunk_index": 0,
            }
        ],
    )
    db.set_embeddings(chunk_ids, np.asarray([vector], dtype="float32"))
    db.finish_document(document_id, 1)
    return document_id, chunk_ids[0]


class ScalingTests(unittest.TestCase):
    def test_fts_index_tracks_chunks_and_document_deletion(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Database(Path(folder) / "test.db")
            if not db.fts_available():
                self.skipTest("SQLite was built without FTS5")
            document_id, chunk_id = add_document(
                db,
                "manual.txt",
                "manual-1",
                "Рабочее давление установки составляет пять мегапаскалей",
                [1.0, 0.0, 0.0],
            )

            results = db.lexical_search_fts(["давление"], 5)
            self.assertEqual(chunk_id, results[0][0])

            db.delete_document(document_id)
            self.assertEqual([], db.lexical_search_fts(["давление"], 5))

    def test_vector_index_appends_new_chunks_without_rebuild(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            db = Database(root / "test.db")
            _, first_chunk = add_document(db, "first.txt", "first", "Первый документ", [1.0, 0.0, 0.0])
            index = VectorIndex(db, root / "vectors.index", root / "vectors.json", "test-model")
            index.rebuild()

            _, second_chunk = add_document(db, "second.txt", "second", "Второй документ", [0.0, 1.0, 0.0])
            with patch.object(index, "rebuild", wraps=index.rebuild) as rebuild:
                index.sync()
                rebuild.assert_not_called()

            self.assertEqual(2, index.index.ntotal)
            self.assertEqual([first_chunk, second_chunk], index.chunk_ids)

    @patch("rag_assistant.retrieval.embed_query", return_value=np.asarray([1.0, 0.0], dtype="float32"))
    def test_large_corpus_expands_candidate_pool(self, _embed_query):
        settings = Settings(
            dense_candidates=40,
            lexical_candidates=40,
            max_search_candidates=120,
            enable_reranker=False,
        )
        db = Mock()
        db.chunk_count.return_value = 100_000
        db.chunks_by_ids.return_value = {}
        index = Mock()
        index.search.return_value = []
        retriever = HybridRetriever(db, index, settings)

        with patch.object(retriever, "_lexical_search", return_value=[]) as lexical:
            retriever.search("давление установки")

        index.search.assert_called_once()
        self.assertEqual(120, index.search.call_args.args[1])
        self.assertEqual(120, lexical.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
