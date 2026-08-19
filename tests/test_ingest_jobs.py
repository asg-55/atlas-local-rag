import threading
import time
import unittest
from unittest.mock import Mock

from rag_assistant.ingest_jobs import IngestJobManager


class IngestJobManagerTests(unittest.TestCase):
    def test_indexing_continues_after_submitter_returns(self):
        started = threading.Event()
        release = threading.Event()
        service = Mock()

        def ingest(_filename, _content):
            started.set()
            release.wait(timeout=2)
            return {"status": "ready", "document_id": "doc-1", "chunks": 7}

        service.ingest.side_effect = ingest
        manager = IngestJobManager(service)
        try:
            job, created = manager.submit("manual.pdf", b"content")
            self.assertTrue(created)
            self.assertTrue(started.wait(timeout=1))
            self.assertIn(
                manager.snapshots()[0]["status"],
                {"queued", "running"},
            )

            duplicate, duplicate_created = manager.submit("renamed.pdf", b"content")
            self.assertFalse(duplicate_created)
            self.assertEqual(job["id"], duplicate["id"])

            release.set()
            deadline = time.monotonic() + 2
            snapshot = manager.snapshots()[0]
            while snapshot["status"] == "running" and time.monotonic() < deadline:
                time.sleep(0.01)
                snapshot = manager.snapshots()[0]

            self.assertEqual("ready", snapshot["status"])
            self.assertEqual(7, snapshot["result"]["chunks"])
            service.ingest.assert_called_once_with("manual.pdf", b"content")
        finally:
            release.set()
            manager.close()

    def test_failure_is_exposed_without_stopping_worker(self):
        service = Mock()
        service.ingest.side_effect = [RuntimeError("bad document"), {"status": "duplicate", "chunks": 2}]
        manager = IngestJobManager(service)
        try:
            manager.submit("bad.pdf", b"bad")
            manager.submit("known.pdf", b"known")
            deadline = time.monotonic() + 2
            snapshots = manager.snapshots()
            while any(job["status"] in {"queued", "running"} for job in snapshots) and time.monotonic() < deadline:
                time.sleep(0.01)
                snapshots = manager.snapshots()

            self.assertEqual(["error", "duplicate"], [job["status"] for job in snapshots])
            self.assertEqual("bad document", snapshots[0]["error"])
        finally:
            manager.close()


if __name__ == "__main__":
    unittest.main()
