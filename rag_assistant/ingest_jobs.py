from __future__ import annotations

import hashlib
import queue
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class IngestJob:
    id: str
    filename: str
    sha256: str
    status: str
    created_at: str
    result: dict | None = None
    error: str | None = None


class IngestJobManager:
    """Process document uploads outside the Streamlit page lifecycle."""

    FINISHED_STATUSES = {"ready", "duplicate", "error"}

    def __init__(self, service):
        self.service = service
        self._jobs: dict[str, IngestJob] = {}
        self._active_hashes: dict[str, str] = {}
        self._queue: queue.Queue[tuple[str, bytes] | None] = queue.Queue()
        self._lock = threading.Lock()
        self._worker = threading.Thread(
            target=self._run,
            name="atlas-document-indexing",
            daemon=True,
        )
        self._worker.start()

    def submit(self, filename: str, content: bytes) -> tuple[dict, bool]:
        digest = hashlib.sha256(content).hexdigest()
        with self._lock:
            active_id = self._active_hashes.get(digest)
            if active_id:
                return asdict(self._jobs[active_id]), False
            job = IngestJob(
                id=str(uuid.uuid4()),
                filename=filename,
                sha256=digest,
                status="queued",
                created_at=_utc_now(),
            )
            self._jobs[job.id] = job
            self._active_hashes[digest] = job.id
        self._queue.put((job.id, content))
        return asdict(job), True

    def snapshots(self) -> list[dict]:
        with self._lock:
            return [asdict(job) for job in self._jobs.values()]

    def clear_finished(self) -> None:
        with self._lock:
            self._jobs = {
                job_id: job
                for job_id, job in self._jobs.items()
                if job.status not in self.FINISHED_STATUSES
            }

    def close(self, timeout: float | None = 5.0) -> None:
        self._queue.put(None)
        self._worker.join(timeout=timeout)

    def __enter__(self) -> "IngestJobManager":
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback) -> None:
        self.close()

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            job_id, content = item
            with self._lock:
                job = self._jobs[job_id]
                job.status = "running"
            try:
                result = self.service.ingest(job.filename, content)
                with self._lock:
                    job.result = result
                    job.status = str(result.get("status") or "ready")
            except Exception as exc:
                with self._lock:
                    job.error = str(exc)
                    job.status = "error"
            finally:
                with self._lock:
                    self._active_hashes.pop(job.sha256, None)
