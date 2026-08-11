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
from .llama_cpp_client import LlamaCppClient
from .ollama_client import ModelRequestError, OllamaClient
from .parsers import parse_file
from .retrieval import HybridRetriever
from .vector_index import VectorIndex


class AssistantService:
    RESPONSE_KINDS = {"conversation", "knowledge", "analysis", "code_create", "code_discuss"}

    @staticmethod
    def _select_attachment_text(text: str, query: str, max_chars: int) -> str:
        if len(text) <= max_chars:
            return text
        sections = [section.strip() for section in text.split("\n\n") if section.strip()]
        overview = [
            (index, section)
            for index, section in enumerate(sections)
            if "Сводка набора данных:" in section
        ]
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
        for index, section in overview:
            remaining = max_chars - used
            if remaining <= 0:
                break
            selected.append((index, section[:remaining]))
            used += min(len(section), remaining)
        for index, section in ranked:
            if any(selected_index == index for selected_index, _ in selected):
                continue
            if selected and used + len(section) > max_chars:
                continue
            selected.append((index, section[:max_chars]))
            used += len(section)
            if used >= max_chars:
                break
        selected.sort(key=lambda item: item[0])
        return "\n\n".join(section for _, section in selected)[:max_chars]

    @staticmethod
    def _has_code_context(history: list[dict]) -> bool:
        return any(
            item["role"] == "assistant"
            and (
                "```" in item["content"]
                or re.search(r"\b(?:Sub|Function|class|def)\s+\w+", item["content"])
            )
            for item in history[-8:]
        )

    @staticmethod
    def _has_structured_attachment(attachments: list[dict]) -> bool:
        return any(
            Path(item["filename"]).suffix.casefold() in {".xlsx", ".csv", ".json"}
            for item in attachments
        )

    @staticmethod
    def _is_analysis_request(question: str) -> bool:
        return bool(
            re.search(
                r"\b(?:проанализируй|анализ|сравни|сопоставь|посчитай|сводк\w*|"
                r"пропуск\w*|отклонени\w*|тенденци\w*|миним\w*|максим\w*|"
                r"средн\w*|таблиц\w*|строк\w*|столбц\w*|данн\w*)\b",
                question.casefold(),
            )
        )

    @staticmethod
    def _is_code_creation(question: str, has_code_context: bool) -> bool:
        normalized = question.casefold()
        creation = re.search(
            r"\b(?:напиши|создай|сделай|сгенерируй|реализуй|исправь|почини|"
            r"добавь|измени|переделай|перепиши|доработай|write|create|fix|update|refactor)\b",
            normalized,
        )
        code_signal = re.search(
            r"\b(?:код|макрос|скрипт|программ\w*|vba|python|c#|csharp|\.net|"
            r"функци\w*|процедур\w*|класс\w*)\b",
            normalized,
        )
        return bool(creation and (code_signal or has_code_context))

    @staticmethod
    def _is_code_discussion(question: str, has_code_context: bool) -> bool:
        if not has_code_context:
            return False
        return bool(
            re.search(
                r"\b(?:почему|зачем|объясни|поясни|что значит|что делает|"
                r"какая ошибка|в ч[её]м ошибка|как работает|чем отличается|"
                r"на каком языке|как использовать|как запустить|куда вставить|где вставить|"
                r"что изменил(?:ось|и)|что поменял(?:ось|и)|какие изменения|"
                r"как (?:можно )?(?:усилить|улучшить|доработать)|что (?:можно )?(?:усилить|улучшить|доработать)|"
                r"какие улучшения|эта строка|этот блок|этот код|why|explain|what does|how does)\b",
                question.casefold(),
            )
        )

    @staticmethod
    def _requests_knowledge(question: str) -> bool:
        return bool(
            re.search(
                r"(?:\b(?:в|из|по)\s+баз[аеы]\s+знаний\b|"
                r"\b(?:в|из|по)\s+документ(?:ах|ам|ов)\b|"
                r"\bсогласно\s+(?:документ\w*|регламент\w*|инструкци\w*)\b|"
                r"\bв\s+выбранном\s+документе\b)",
                question.casefold(),
            )
        )

    @staticmethod
    def _starts_new_topic(question: str) -> bool:
        return bool(
            re.search(
                r"(?:^|[.!?]\s*)(?:спасибо[,.]?\s+)?(?:а\s+)?теперь\b|"
                r"(?:^|[.!?]\s*)(?:другой|новый|отдельный)\s+вопрос\b|"
                r"(?:^|[.!?]\s*)сменим\s+тему\b|"
                r"(?:^|[.!?]\s*)(?:now|new question|let'?s change the subject)\b",
                question.casefold(),
            )
        )

    @classmethod
    def _response_kind(
        cls,
        question: str,
        history: list[dict],
        attachments: list[dict],
        interpretation: dict,
        document_id: str | None,
        use_rag: bool | None,
    ) -> str:
        has_code_context = cls._has_code_context(history)
        if cls._is_code_creation(question, has_code_context):
            return "code_create"
        if cls._is_code_discussion(question, has_code_context):
            return "code_discuss"

        suggested = str(interpretation.get("response_kind") or "").strip().casefold()
        if suggested in {"code_create", "code_discuss"}:
            return suggested
        if cls._has_structured_attachment(attachments) and cls._is_analysis_request(question):
            return "analysis"
        if suggested in cls.RESPONSE_KINDS:
            return suggested
        if cls._has_structured_attachment(attachments):
            return "analysis"
        if document_id or interpretation.get("use_knowledge") or use_rag is True:
            return "knowledge"
        return "conversation"

    def __init__(self, settings: Settings):
        self.settings = settings
        settings.ensure_directories()
        self.db = Database(settings.db_path)
        self.index = VectorIndex(self.db, settings.index_path, settings.index_meta_path, settings.embedding_model)
        self.retriever = HybridRetriever(self.db, self.index, settings)
        if settings.llm_backend == "llama_cpp":
            self.ollama = LlamaCppClient(settings.llama_base_url, settings.chat_model)
        elif settings.llm_backend == "ollama":
            self.ollama = OllamaClient(settings.ollama_base_url, settings.chat_model)
        else:
            raise ValueError(f"Неизвестный LLM_BACKEND: {settings.llm_backend}")

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
        use_rag: bool | None = True,
        code_model: str | None = None,
        retry_message_id: int | None = None,
    ) -> tuple[str, list[dict], str]:
        previous_rows = [dict(row) for row in self.db.messages(conversation_id, limit=9)]
        if retry_message_id is not None:
            retry_row = self.db.message(retry_message_id)
            if (
                retry_row is None
                or retry_row["conversation_id"] != conversation_id
                or retry_row["role"] != "user"
                or not previous_rows
                or previous_rows[-1]["id"] != retry_message_id
            ):
                raise ValueError("Повторить можно только последний вопрос без ответа.")
            question = retry_row["content"]
            history = [row for row in previous_rows if row["id"] != retry_message_id][-8:]
            user_message_id = retry_message_id
            self.db.set_message_status(user_message_id, "pending")
        else:
            history = previous_rows[-8:]
            user_message_id = self.db.add_message(
                conversation_id, "user", question, status="pending"
            )

        try:
            answer, sources, standalone = self._generate_answer(
                conversation_id,
                question,
                history,
                strict=strict,
                model=model,
                temperature=temperature,
                num_predict=num_predict,
                top_p=top_p,
                num_ctx=num_ctx,
                final_k=final_k,
                answer_mode=answer_mode,
                custom_instruction=custom_instruction,
                document_id=document_id,
                think=think,
                use_rag=use_rag,
                code_model=code_model,
            )
        except Exception as exc:
            self.db.set_message_status(user_message_id, "failed", str(exc)[:2000])
            raise

        self.db.add_message(conversation_id, "assistant", answer, sources)
        self.db.set_message_status(user_message_id, "complete")
        if not history:
            self.db.rename_conversation(conversation_id, question[:70])
        return answer, sources, standalone

    def _generate_answer(
        self,
        conversation_id: str,
        question: str,
        history: list[dict],
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
        use_rag: bool | None = True,
        code_model: str | None = None,
    ) -> tuple[str, list[dict], str]:
        attachments = [dict(row) for row in self.db.list_chat_attachments(conversation_id)]
        has_code_context = self._has_code_context(history)
        if answer_mode == "Рабочий код" or self._is_code_creation(question, has_code_context):
            interpretation = {
                "intent": "Написание рабочего кода",
                "search_query": question,
                "needs_clarification": False,
                "clarifying_question": "",
                "response_kind": "code_create",
                "use_knowledge": self._requests_knowledge(question),
            }
        else:
            interpretation = self.ollama.interpret_question(
                question,
                history,
                model=model,
                document_selected=document_id is not None,
            )
        response_kind = self._response_kind(
            question,
            history,
            attachments,
            interpretation,
            document_id,
            use_rag,
        )
        explicit_knowledge_request = (
            document_id is not None or self._requests_knowledge(question)
        )
        if (
            attachments
            and use_rag is None
            and not explicit_knowledge_request
            and response_kind == "knowledge"
        ):
            response_kind = "conversation"
        active_attachments = (
            attachments
            if response_kind in {"analysis", "code_create", "code_discuss"}
            else []
        )
        should_search = (
            use_rag is True
            or (
                use_rag is None
                and (
                    document_id is not None
                    or (
                        bool(interpretation.get("use_knowledge"))
                        and (
                            not attachments
                            or explicit_knowledge_request
                            or response_kind == "analysis"
                        )
                    )
                    or response_kind == "knowledge"
                )
            )
        )
        standalone = interpretation["search_query"]
        if interpretation["needs_clarification"] and should_search:
            answer = interpretation["clarifying_question"]
            return answer, [], standalone
        results = (
            self.retriever.search(
                standalone,
                final_k=final_k,
                document_id=document_id,
                include_all=answer_mode == "Извлечь все данные" and document_id is not None,
            )
            if should_search
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
        direct_budget = context_budget if not should_search else int(context_budget * 0.65)
        per_attachment_budget = max(
            1000, direct_budget // max(1, len(active_attachments))
        )
        for attachment in active_attachments:
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
        effective_strict = strict and should_search
        answer_history = (
            []
            if response_kind == "conversation" and self._starts_new_topic(question)
            else history
        )
        if not results and not bounded_attachments and effective_strict:
            answer = "В загруженных документах информация не найдена."
            sources: list[dict] = []
        else:
            effective_mode = answer_mode
            if response_kind == "code_create":
                effective_mode = "Рабочий код"
            elif response_kind == "code_discuss":
                effective_mode = "Обсуждение кода"
            elif response_kind == "analysis":
                effective_mode = "Аналитический разбор"
                analysis_rule = (
                    "Опирайся на вычисленную сводку по всему набору данных. "
                    "Отделяй факты из сводки от наблюдений по отдельным строкам; "
                    "не делай точных расчётов, которых нет в источнике."
                )
                custom_instruction = "\n".join(
                    value for value in [custom_instruction.strip(), analysis_rule] if value
                )
            effective_model = (
                (code_model or model)
                if response_kind in {"code_create", "code_discuss"}
                else model
            )
            effective_num_ctx, effective_num_predict = self._safe_model_limits(
                effective_model, num_ctx, num_predict
            )
            answer_options = {
                "strict": effective_strict,
                "model": effective_model,
                "temperature": temperature,
                "num_predict": effective_num_predict,
                "top_p": top_p,
                "num_ctx": effective_num_ctx,
                "answer_mode": effective_mode,
                "custom_instruction": custom_instruction,
                "think": think,
                "attachments": bounded_attachments,
            }
            try:
                answer = self.ollama.answer(
                    question, results, answer_history, **answer_options
                )
            except ModelRequestError:
                fallback_model = model or getattr(self.ollama, "model", None)
                if (
                    response_kind not in {"code_create", "code_discuss"}
                    or not fallback_model
                    or fallback_model == effective_model
                ):
                    raise
                fallback_ctx, fallback_predict = self._safe_model_limits(
                    fallback_model, num_ctx, num_predict
                )
                answer_options.update(
                    model=fallback_model,
                    num_ctx=fallback_ctx,
                    num_predict=fallback_predict,
                )
                answer = self.ollama.answer(
                    question, results, answer_history, **answer_options
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
        return answer, sources, standalone

    def _safe_model_limits(
        self, model: str | None, num_ctx: int, num_predict: int
    ) -> tuple[int, int]:
        try:
            model_context = int(self.ollama.context_length(model))
        except (AttributeError, TypeError, ValueError):
            model_context = num_ctx
        safe_context = max(1024, min(num_ctx, model_context))
        response_reserve = min(4096, max(512, safe_context // 4))
        safe_predict = max(256, min(num_predict, safe_context - response_reserve))
        return safe_context, safe_predict

    @staticmethod
    def decode_sources(row) -> list[dict]:
        value = row["sources_json"]
        return json.loads(value) if value else []
