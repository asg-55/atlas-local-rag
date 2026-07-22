from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Sequence

from .models import Chunk


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    sha256 TEXT NOT NULL UNIQUE,
                    extension TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    stored_path TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'processing',
                    error TEXT,
                    chunk_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS chunks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    filename TEXT NOT NULL,
                    content TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    location TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    embedding BLOB,
                    UNIQUE(document_id, content_hash, location)
                );
                CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks(document_id);
                CREATE INDEX IF NOT EXISTS idx_chunks_hash ON chunks(content_hash);
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    sources_json TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_messages_conversation
                    ON messages(conversation_id, id);
                CREATE TABLE IF NOT EXISTS app_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS report_jobs (
                    id TEXT PRIMARY KEY,
                    file_hash TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    page_start INTEGER NOT NULL,
                    page_end INTEGER NOT NULL,
                    dpi INTEGER NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('queued', 'running', 'completed', 'failed')),
                    current_page INTEGER NOT NULL,
                    total_pages INTEGER NOT NULL,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    UNIQUE(file_hash, page_start, page_end, dpi)
                );
                CREATE INDEX IF NOT EXISTS idx_report_jobs_status
                    ON report_jobs(status, created_at);
                CREATE TABLE IF NOT EXISTS report_job_pages (
                    job_id TEXT NOT NULL REFERENCES report_jobs(id) ON DELETE CASCADE,
                    page_number INTEGER NOT NULL,
                    report_json TEXT NOT NULL,
                    journal_json TEXT NOT NULL,
                    warnings_json TEXT NOT NULL,
                    error TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(job_id, page_number)
                );
                """
            )

    def find_document_by_hash(self, sha256: str):
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM documents WHERE sha256=?", (sha256,)
            ).fetchone()

    def create_document(
        self, filename: str, sha256: str, extension: str, size_bytes: int, stored_path: str
    ) -> str:
        doc_id = str(uuid.uuid4())
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO documents
                (id, filename, sha256, extension, size_bytes, stored_path, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (doc_id, filename, sha256, extension, size_bytes, stored_path, now, now),
            )
        return doc_id

    def finish_document(self, doc_id: str, chunk_count: int) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE documents SET status='ready', error=NULL, chunk_count=?, updated_at=? WHERE id=?",
                (chunk_count, utc_now(), doc_id),
            )

    def fail_document(self, doc_id: str, error: str) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM chunks WHERE document_id=?", (doc_id,))
            conn.execute(
                "UPDATE documents SET status='error', error=?, updated_at=? WHERE id=?",
                (error[:2000], utc_now(), doc_id),
            )

    def add_chunks(self, doc_id: str, filename: str, rows: Sequence[dict]) -> list[int]:
        ids: list[int] = []
        with self.connect() as conn:
            for row in rows:
                cursor = conn.execute(
                    """INSERT OR IGNORE INTO chunks
                    (document_id, filename, content, content_hash, location, chunk_index)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        doc_id,
                        filename,
                        row["content"],
                        row["content_hash"],
                        row["location"],
                        row["chunk_index"],
                    ),
                )
                if cursor.lastrowid:
                    ids.append(int(cursor.lastrowid))
        return ids

    def set_embeddings(self, chunk_ids: Sequence[int], embeddings) -> None:
        with self.connect() as conn:
            conn.executemany(
                "UPDATE chunks SET embedding=? WHERE id=?",
                [(vector.astype("float32").tobytes(), chunk_id) for chunk_id, vector in zip(chunk_ids, embeddings)],
            )
            self._bump_generation(conn)

    def list_documents(self):
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM documents ORDER BY created_at DESC"
            ).fetchall()

    def get_document(self, doc_id: str):
        with self.connect() as conn:
            return conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()

    def delete_document(self, doc_id: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute("SELECT stored_path FROM documents WHERE id=?", (doc_id,)).fetchone()
            if not row:
                return None
            conn.execute("DELETE FROM documents WHERE id=?", (doc_id,))
            self._bump_generation(conn)
            return str(row["stored_path"])

    def all_chunks(self, only_embedded: bool = False):
        query = """SELECT c.* FROM chunks c
        JOIN documents d ON d.id=c.document_id WHERE d.status='ready'"""
        if only_embedded:
            query += " AND c.embedding IS NOT NULL"
        query += " ORDER BY c.id"
        with self.connect() as conn:
            return conn.execute(query).fetchall()

    def chunks_by_ids(self, ids: Sequence[int]) -> dict[int, Chunk]:
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT id, document_id, filename, content, location, chunk_index FROM chunks WHERE id IN ({placeholders})",
                tuple(ids),
            ).fetchall()
        return {int(r["id"]): Chunk(**dict(r)) for r in rows}

    def chunks_for_document(self, document_id: str) -> list[Chunk]:
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT id, document_id, filename, content, location, chunk_index
                FROM chunks WHERE document_id=? ORDER BY chunk_index""",
                (document_id,),
            ).fetchall()
        return [Chunk(**dict(row)) for row in rows]

    def stats(self) -> dict:
        with self.connect() as conn:
            docs = conn.execute("SELECT COUNT(*) FROM documents WHERE status='ready'").fetchone()[0]
            chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            embedded = conn.execute("SELECT COUNT(*) FROM chunks WHERE embedding IS NOT NULL").fetchone()[0]
            conversations = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        return {"documents": docs, "chunks": chunks, "embedded": embedded, "conversations": conversations}

    def create_conversation(self, title: str = "Новый диалог") -> str:
        conversation_id = str(uuid.uuid4())
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO conversations (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (conversation_id, title, now, now),
            )
        return conversation_id

    def list_conversations(self):
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM conversations ORDER BY updated_at DESC"
            ).fetchall()

    def rename_conversation(self, conversation_id: str, title: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE conversations SET title=?, updated_at=? WHERE id=?",
                (title[:120], utc_now(), conversation_id),
            )

    def delete_conversation(self, conversation_id: str) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM conversations WHERE id=?", (conversation_id,))

    def add_message(self, conversation_id: str, role: str, content: str, sources=None) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO messages (conversation_id, role, content, sources_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (conversation_id, role, content, json.dumps(sources, ensure_ascii=False) if sources else None, utc_now()),
            )
            conn.execute(
                "UPDATE conversations SET updated_at=? WHERE id=?",
                (utc_now(), conversation_id),
            )

    def messages(self, conversation_id: str, limit: int | None = None):
        query = "SELECT * FROM messages WHERE conversation_id=? ORDER BY id"
        params: list = [conversation_id]
        if limit:
            query = f"SELECT * FROM ({query.replace('ORDER BY id', 'ORDER BY id DESC')} LIMIT ?) ORDER BY id"
            params.append(limit)
        with self.connect() as conn:
            return conn.execute(query, params).fetchall()

    def generation(self) -> int:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM app_meta WHERE key='generation'").fetchone()
            return int(row[0]) if row else 0

    def create_or_get_report_job(
        self,
        file_hash: str,
        filename: str,
        source_path: str,
        page_start: int,
        page_end: int,
        dpi: int,
    ):
        job_id = str(uuid.uuid4())
        now = utc_now()
        with self.connect() as conn:
            cursor = conn.execute(
                """INSERT OR IGNORE INTO report_jobs
                (id, file_hash, filename, source_path, page_start, page_end, dpi,
                 status, current_page, total_pages, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?)""",
                (
                    job_id,
                    file_hash,
                    filename,
                    source_path,
                    page_start,
                    page_end,
                    dpi,
                    page_start - 1,
                    page_end - page_start + 1,
                    now,
                    now,
                ),
            )
            created = cursor.rowcount == 1
            row = conn.execute(
                """SELECT * FROM report_jobs
                WHERE file_hash=? AND page_start=? AND page_end=? AND dpi=?""",
                (file_hash, page_start, page_end, dpi),
            ).fetchone()
        return row, created

    def get_report_job(self, job_id: str):
        with self.connect() as conn:
            return conn.execute("SELECT * FROM report_jobs WHERE id=?", (job_id,)).fetchone()

    def latest_report_job(self):
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM report_jobs ORDER BY created_at DESC LIMIT 1"
            ).fetchone()

    def recover_report_jobs(self) -> None:
        with self.connect() as conn:
            conn.execute(
                """UPDATE report_jobs SET status='queued', error=NULL, updated_at=?
                WHERE status='running'""",
                (utc_now(),),
            )

    def claim_next_report_job(self):
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """SELECT * FROM report_jobs WHERE status='queued'
                ORDER BY created_at LIMIT 1"""
            ).fetchone()
            if not row:
                return None
            conn.execute(
                "UPDATE report_jobs SET status='running', error=NULL, updated_at=? WHERE id=?",
                (utc_now(), row["id"]),
            )
            return conn.execute("SELECT * FROM report_jobs WHERE id=?", (row["id"],)).fetchone()

    def report_job_completed_pages(self, job_id: str) -> set[int]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT page_number FROM report_job_pages WHERE job_id=?", (job_id,)
            ).fetchall()
        return {int(row["page_number"]) for row in rows}

    def save_report_job_page(
        self,
        job_id: str,
        page_number: int,
        report: dict,
        journal: list[dict],
        warnings: list[str],
        error: str | None = None,
    ) -> None:
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO report_job_pages
                (job_id, page_number, report_json, journal_json, warnings_json, error, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id, page_number) DO UPDATE SET
                    report_json=excluded.report_json,
                    journal_json=excluded.journal_json,
                    warnings_json=excluded.warnings_json,
                    error=excluded.error,
                    updated_at=excluded.updated_at""",
                (
                    job_id,
                    page_number,
                    json.dumps(report, ensure_ascii=False, default=str),
                    json.dumps(journal, ensure_ascii=False, default=str),
                    json.dumps(warnings, ensure_ascii=False),
                    error[:2000] if error else None,
                    now,
                ),
            )
            conn.execute(
                "UPDATE report_jobs SET current_page=?, updated_at=? WHERE id=?",
                (page_number, now, job_id),
            )

    def report_job_pages(self, job_id: str):
        with self.connect() as conn:
            return conn.execute(
                """SELECT * FROM report_job_pages WHERE job_id=?
                ORDER BY page_number""",
                (job_id,),
            ).fetchall()

    def finish_report_job(self, job_id: str) -> None:
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """UPDATE report_jobs SET status='completed', error=NULL,
                completed_at=?, updated_at=? WHERE id=?""",
                (now, now, job_id),
            )

    def fail_report_job(self, job_id: str, error: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """UPDATE report_jobs SET status='failed', error=?, updated_at=?
                WHERE id=?""",
                (error[:2000], utc_now(), job_id),
            )

    @staticmethod
    def _bump_generation(conn: sqlite3.Connection) -> None:
        conn.execute(
            """INSERT INTO app_meta(key, value) VALUES ('generation', '1')
            ON CONFLICT(key) DO UPDATE SET value=CAST(value AS INTEGER)+1"""
        )
