import tempfile
import unittest
from pathlib import Path

from rag_assistant.chunking import make_chunks, normalize_text, split_text
from rag_assistant.database import Database
from rag_assistant.models import TextBlock


class ChunkingTests(unittest.TestCase):
    def test_normalizes_and_splits_with_overlap(self):
        text = "Раздел   один. " * 200
        chunks = split_text(text, max_chars=300, overlap=40)
        self.assertGreater(len(chunks), 2)
        self.assertTrue(all(0 < len(chunk) <= 300 for chunk in chunks))
        self.assertNotIn("  ", normalize_text(text))

    def test_deduplicates_inside_document(self):
        blocks = [TextBlock("одинаковый фрагмент", "стр. 1"), TextBlock("одинаковый фрагмент", "стр. 2")]
        chunks = make_chunks(blocks)
        self.assertEqual(1, len(chunks))

class DatabaseTests(unittest.TestCase):
    def test_persistent_conversation_and_document_deduplication(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Database(Path(folder) / "test.db")
            conversation = db.create_conversation("Проверка")
            db.add_message(conversation, "user", "Вопрос")
            self.assertEqual("Вопрос", db.messages(conversation)[0]["content"])
            doc_id = db.create_document("a.pdf", "abc", ".pdf", 10, "a.pdf")
            self.assertEqual(doc_id, db.find_document_by_hash("abc")["id"])


if __name__ == "__main__":
    unittest.main()
