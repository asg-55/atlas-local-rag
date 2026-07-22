from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path

from .chunking import make_chunks
from .config import Settings
from .database import Database
from .embeddings import embed_passages
from .ollama_client import OllamaClient
from .parsers import parse_file
from .retrieval import HybridRetriever
from .vector_index import VectorIndex


class AssistantService:
    @staticmethod
    def _select_attachment_text(text: str, query: str, max_chars: int) -> str:
        if len(text) <= max_chars:
            return text
        sections = [section.strip() for section in text.split("\n\n") if section.strip()]
        terms = {
            token.casefold()
            for token in re.findall(r"[\wА-Яа-яЁё-]+", query)
            if len(token) >= 4
        }
        ranked = sorted(
            enumerate(sections),
            key=lambda item: (
                sum(item[1].casefold().count(term) for term in terms),
                -item[0],
            ),
            reverse=True,
        )
        selected: list[tuple[int, str]] = []
        used = 0
        for index, section in ranked:
            if selected and used + len(section) > max_chars:
                continue
            selected.append((index, section[:max_chars]))
            used += len(section)
            if used >= max_chars:
                break
        selected.sort(key=lambda item: item[0])
        return "\n\n".join(section for _, section in selected)[:max_chars]

    def __init__(self, settings: Settings):
        self.settings = settings
        settings.ensure_directories()
        self.db = Database(settings.db_path)
        self.index = VectorIndex(self.db, settings.index_path, settings.index_meta_path, settings.embedding_model)
        self.retriever = HybridRetriever(self.db, self.index, settings)
        self.ollama = OllamaClient(settings.ollama_base_url, settings.chat_model)

    def ingest(self, filename: str, content: bytes) -> dict:
        digest = hashlib.sha256(content).hexdigest()
        existing = self.db.find_document_by_hash(digest)
        if existing:
            return {"status": "duplicate", "document_id": existing["id"], "chunks": existing["chunk_count"]}
        safe_name = Path(filename).name
        extension = Path(safe_name).suffix.lower()
        placeholder = self.settings.documents_dir / "pending"
        doc_id = self.db.create_document(safe_name, digest, extension, len(content), str(placeholder))
        document_dir = self.settings.documents_dir / doc_id
        document_dir.mkdir(parents=True, exist_ok=False)
        stored_path = document_dir / safe_name
        stored_path.write_bytes(content)
        with self.db.connect() as conn:
            conn.execute("UPDATE documents SET stored_path=? WHERE id=?", (str(stored_path), doc_id))
        try:
            blocks = parse_file(stored_path)
            rows = make_chunks(blocks)
            if not rows:
                raise ValueError("В документе не найден текст")
            chunk_ids = self.db.add_chunks(doc_id, safe_name, rows)
            embeddings = embed_passages([row["content"] for row in rows], self.settings.embedding_model)
            self.db.set_embeddings(chunk_ids, embeddings[: len(chunk_ids)])
            self.db.finish_document(doc_id, len(chunk_ids))
            self.index.sync()
            return {"status": "ready", "document_id": doc_id, "chunks": len(chunk_ids)}
        except Exception as exc:
            self.db.fail_document(doc_id, str(exc))
            raise

    def delete_document(self, doc_id: str) -> bool:
        stored_path = self.db.delete_document(doc_id)
        if not stored_path:
            return False
        path = Path(stored_path).resolve()
        root = self.settings.documents_dir.resolve()
        if root in path.parents and path.parent.exists():
            shutil.rmtree(path.parent)
        self.index.rebuild()
        return True

    def attach_to_conversation(self, conversation_id: str, filename: str, content: bytes):
        digest = hashlib.sha256(content).hexdigest()
        for row in self.db.list_chat_attachments(conversation_id):
            if row["sha256"] == digest:
                return row, False
        safe_name = Path(filename).name
        attachment_dir = self.settings.chat_attachments_dir / conversation_id
        attachment_dir.mkdir(parents=True, exist_ok=True)
        stored_path = attachment_dir / f"{digest[:12]}-{safe_name}"
        stored_path.write_bytes(content)
        try:
            blocks = parse_file(stored_path)
            extracted_text = "\n\n".join(
                f"[{block.location}]\n{block.text.strip()}"
                for block in blocks
                if block.text.strip()
            )
            if not extracted_text:
                raise ValueError("Во вложении не найден текст")
            return self.db.create_or_get_chat_attachment(
                conversation_id,
                safe_name,
                digest,
                len(content),
                str(stored_path),
                extracted_text,
            )
        except Exception:
            stored_path.unlink(missing_ok=True)
            raise

    def delete_chat_attachment(self, conversation_id: str, attachment_id: str) -> bool:
        stored_path = self.db.delete_chat_attachment(attachment_id, conversation_id)
        if not stored_path:
            return False
        path = Path(stored_path).resolve()
        root = self.settings.chat_attachments_dir.resolve()
        if root in path.parents:
            path.unlink(missing_ok=True)
            if path.parent != root and path.parent.exists() and not any(path.parent.iterdir()):
                path.parent.rmdir()
        return True

    def delete_conversation(self, conversation_id: str) -> None:
        self.db.delete_conversation(conversation_id)
        attachment_dir = (self.settings.chat_attachments_dir / conversation_id).resolve()
        root = self.settings.chat_attachments_dir.resolve()
        if root in attachment_dir.parents and attachment_dir.exists():
            shutil.rmtree(attachment_dir)

    def answer(
        self,
        conversation_id: str,
        question: str,
        strict: bool = True,
        model: str | None = None,
        temperature: float = 0.2,
        num_predict: int = 2200,
        top_p: float = 0.9,
        num_ctx: int = 16384,
        final_k: int | None = None,
        answer_mode: str = "Подробный ответ",
        custom_instruction: str = "",
        document_id: str | None = None,
        think: bool = False,
        use_rag: bool = True,
    ) -> tuple[str, list[dict], str]:
        previous_rows = self.db.messages(conversation_id, limit=8)
        history = [dict(row) for row in previous_rows]
        attachments = [dict(row) for row in self.db.list_chat_attachments(conversation_id)]
        interpretation = self.ollama.interpret_question(
            question,
            history,
            model=model,
            document_selected=document_id is not None or bool(attachments),
        )
        standalone = interpretation["search_query"]
        self.db.add_message(conversation_id, "user", question)
        if interpretation["needs_clarification"]:
            answer = interpretation["clarifying_question"]
            self.db.add_message(conversation_id, "assistant", answer)
            if not history:
                self.db.rename_conversation(conversation_id, question[:70])
            return answer, [], standalone
        results = (
            self.retriever.search(
                standalone,
                final_k=final_k,
                document_id=document_id,
                include_all=answer_mode == "Извлечь все данные" and document_id is not None,
            )
            if use_rag
            else []
        )
        context_size = 0
        bounded_results = []
        history_chars = sum(len(item["content"][:1000]) for item in history[-6:])
        reserved_chars = 8000 + history_chars + len(question)
        available_chars = max(3000, int(max(1024, num_ctx - num_predict) * 2.5) - reserved_chars)
        desired_chars = max(self.settings.max_context_chars, int(num_ctx * 2.2))
        context_budget = min(120000, desired_chars, available_chars)
        bounded_attachments = []
        direct_budget = context_budget if not use_rag else int(context_budget * 0.65)
        per_attachment_budget = max(
            1000, direct_budget // max(1, len(attachments))
        )
        for attachment in attachments:
            remaining = direct_budget - context_size
            if remaining <= 0:
                break
            attachment = dict(attachment)
            attachment["extracted_text"] = self._select_attachment_text(
                attachment["extracted_text"],
                standalone,
                min(remaining, per_attachment_budget),
            )
            bounded_attachments.append(attachment)
            context_size += len(attachment["extracted_text"])
        for result in results:
            if context_size + len(result.chunk.content) > context_budget:
                break
            bounded_results.append(result)
            context_size += len(result.chunk.content)
        results = bounded_results
        if not results and not bounded_attachments and strict:
            answer = "В загруженных документах информация не найдена."
            sources: list[dict] = []
        else:
            answer = self.ollama.answer(
                question,
                results,
                history,
                strict=strict,
                model=model,
                temperature=temperature,
                num_predict=num_predict,
                top_p=top_p,
                num_ctx=num_ctx,
                answer_mode=answer_mode,
                custom_instruction=custom_instruction,
                think=think,
                attachments=bounded_attachments,
            )
            sources = [
                {
                    "filename": attachment["filename"],
                    "location": "вложение диалога · без индексации",
                    "excerpt": attachment["extracted_text"][:360],
                    "score": 1.0,
                }
                for attachment in bounded_attachments
            ] + [result.as_source() for result in results]
        self.db.add_message(conversation_id, "assistant", answer, sources)
        messages = self.db.messages(conversation_id)
        if len(messages) == 2:
            self.db.rename_conversation(conversation_id, question[:70])
        return answer, sources, standalone

    @staticmethod
    def decode_sources(row) -> list[dict]:
        value = row["sources_json"]
        return json.loads(value) if value else []
