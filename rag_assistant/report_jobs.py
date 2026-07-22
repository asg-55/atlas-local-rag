from __future__ import annotations

import hashlib
import io
import json
import threading
from pathlib import Path

import fitz
import pandas as pd
from PIL import Image

from .database import Database
from .report_extractor import JOURNAL_COLUMNS, REPORT_COLUMNS, extract_report_page


QUALITY_COLUMNS = ["Файл", "Страница", "Предупреждение"]


class ReportJobManager:
    """Persistent, single-worker queue for production report OCR."""

    def __init__(self, db: Database, data_dir: Path):
        self.db = db
        self.source_dir = data_dir / "report_jobs"
        self.source_dir.mkdir(parents=True, exist_ok=True)
        self._wake = threading.Event()
        self.db.recover_report_jobs()
        self._worker = threading.Thread(
            target=self._run, name="atlas-report-ocr", daemon=True
        )
        self._worker.start()

    def submit(
        self,
        filename: str,
        content: bytes,
        page_start: int,
        page_end: int,
        dpi: int,
    ):
        if page_start < 1 or page_end < page_start:
            raise ValueError("Некорректный диапазон страниц")
        if dpi < 1:
            raise ValueError("DPI должен быть положительным")
        file_hash = hashlib.sha256(content).hexdigest()
        source_path = self.source_dir / f"{file_hash}.pdf"
        if not source_path.exists():
            temporary = source_path.with_suffix(".pdf.tmp")
            temporary.write_bytes(content)
            temporary.replace(source_path)
        job, created = self.db.create_or_get_report_job(
            file_hash=file_hash,
            filename=Path(filename).name,
            source_path=str(source_path),
            page_start=page_start,
            page_end=page_end,
            dpi=dpi,
        )
        self._wake.set()
        return job, created

    def result_frames(self, job_id: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        reports: list[dict] = []
        journal: list[dict] = []
        quality: list[dict] = []
        for row in self.db.report_job_pages(job_id):
            report = json.loads(row["report_json"])
            reports.append(report)
            journal.extend(json.loads(row["journal_json"]))
            warnings = json.loads(row["warnings_json"])
            quality.extend(
                {"Файл": report.get("Файл"), "Страница": row["page_number"], "Предупреждение": warning}
                for warning in warnings
            )
        return (
            pd.DataFrame(reports, columns=REPORT_COLUMNS),
            pd.DataFrame(journal, columns=JOURNAL_COLUMNS),
            pd.DataFrame(quality, columns=QUALITY_COLUMNS),
        )

    def _run(self) -> None:
        while True:
            job = self.db.claim_next_report_job()
            if job is None:
                self._wake.wait(timeout=2.0)
                self._wake.clear()
                continue
            self._process(job)

    def _process(self, job) -> None:
        job_id = str(job["id"])
        try:
            completed = self.db.report_job_completed_pages(job_id)
            with fitz.open(str(job["source_path"])) as pdf:
                if int(job["page_end"]) > len(pdf):
                    raise ValueError(f"В PDF только {len(pdf)} страниц")
                for page_number in range(int(job["page_start"]), int(job["page_end"]) + 1):
                    if page_number in completed:
                        continue
                    try:
                        pix = pdf[page_number - 1].get_pixmap(dpi=int(job["dpi"]), alpha=False)
                        image = Image.open(io.BytesIO(pix.tobytes("png")))
                        report, rows, warnings = extract_report_page(
                            image, str(job["filename"]), page_number
                        )
                        self.db.save_report_job_page(
                            job_id, page_number, report, rows, warnings
                        )
                    except Exception as exc:
                        message = str(exc)
                        report = {column: None for column in REPORT_COLUMNS}
                        report.update(
                            {
                                "Файл": str(job["filename"]),
                                "Страница": page_number,
                                "Статус": "Ошибка",
                            }
                        )
                        self.db.save_report_job_page(
                            job_id, page_number, report, [], [message], error=message
                        )
            self.db.finish_report_job(job_id)
        except Exception as exc:
            self.db.fail_report_job(job_id, str(exc))
