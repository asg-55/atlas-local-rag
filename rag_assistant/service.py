from __future__ import annotations

import hashlib
import json
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
            self.index.rebuild()
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
    ) -> tuple[str, list[dict], str]:
        previous_rows = self.db.messages(conversation_id, limit=8)
        history = [dict(row) for row in previous_rows]
        interpretation = self.ollama.interpret_question(
            question,
            history,
            model=model,
            document_selected=document_id is not None,
        )
        standalone = interpretation["search_query"]
        self.db.add_message(conversation_id, "user", question)
        if interpretation["needs_clarification"]:
            answer = interpretation["clarifying_question"]
            self.db.add_message(conversation_id, "assistant", answer)
            if not history:
                self.db.rename_conversation(conversation_id, question[:70])
            return answer, [], standalone
        results = self.retriever.search(
            standalone,
            final_k=final_k,
            document_id=document_id,
            include_all=answer_mode == "Извлечь все данные" and document_id is not None,
        )
        context_size = 0
        bounded_results = []
        history_chars = sum(len(item["content"][:1000]) for item in history[-6:])
        reserved_chars = 8000 + history_chars + len(question)
        available_chars = max(3000, int(max(1024, num_ctx - num_predict) * 2.5) - reserved_chars)
        desired_chars = max(self.settings.max_context_chars, int(num_ctx * 2.2))
        context_budget = min(120000, desired_chars, available_chars)
        for result in results:
            if bounded_results and context_size + len(result.chunk.content) > context_budget:
                break
            bounded_results.append(result)
            context_size += len(result.chunk.content)
        results = bounded_results
        if not results and strict:
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
            )
            sources = [result.as_source() for result in results]
        self.db.add_message(conversation_id, "assistant", answer, sources)
        messages = self.db.messages(conversation_id)
        if len(messages) == 2:
            self.db.rename_conversation(conversation_id, question[:70])
        return answer, sources, standalone

    @staticmethod
    def decode_sources(row) -> list[dict]:
        value = row["sources_json"]
        return json.loads(value) if value else []
