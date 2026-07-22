import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import fitz

from rag_assistant.database import Database
from rag_assistant.report_extractor import REPORT_COLUMNS
from rag_assistant.report_jobs import ReportJobManager


class ReportJobManagerTests(unittest.TestCase):
    def test_job_is_deduplicated_and_page_failure_does_not_stop_batch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = Database(root / "assistant.db")
            pdf = fitz.open()
            for _ in range(3):
                pdf.new_page(width=100, height=100)
            content = pdf.tobytes()
            pdf.close()

            calls: list[int] = []

            def fake_extract(_image, filename, page_number):
                calls.append(page_number)
                if page_number == 2:
                    raise RuntimeError("damaged page")
                report = {column: None for column in REPORT_COLUMNS}
                report.update({"Файл": filename, "Страница": page_number, "Статус": "Готово"})
                return report, [], []

            with patch("rag_assistant.report_jobs.extract_report_page", side_effect=fake_extract):
                manager = ReportJobManager(db, root)
                first, created = manager.submit("batch.pdf", content, 1, 3, 180)
                duplicate, duplicate_created = manager.submit("renamed.pdf", content, 1, 3, 180)
                self.assertTrue(created)
                self.assertFalse(duplicate_created)
                self.assertEqual(first["id"], duplicate["id"])

                deadline = time.monotonic() + 5
                job = db.get_report_job(first["id"])
                while job["status"] not in {"completed", "failed"} and time.monotonic() < deadline:
                    time.sleep(0.05)
                    job = db.get_report_job(first["id"])

            self.assertEqual("completed", job["status"])
            self.assertEqual([1, 2, 3], calls)
            pages = db.report_job_pages(first["id"])
            self.assertEqual(3, len(pages))
            self.assertIsNotNone(pages[1]["error"])
            reports, journal, quality = manager.result_frames(first["id"])
            self.assertEqual(["Готово", "Ошибка", "Готово"], reports["Статус"].tolist())
            self.assertTrue(journal.empty)
            self.assertEqual(1, len(quality))


if __name__ == "__main__":
    unittest.main()
