import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, call

from rag_assistant.config import Settings
from rag_assistant.database import Database
from rag_assistant.service import AssistantService


class AssistantServiceTests(unittest.TestCase):
    @staticmethod
    def _routed_service(history, attachments=None):
        service = AssistantService.__new__(AssistantService)
        service.db = Mock()
        service.db.messages.side_effect = [
            history,
            history + [{"role": "user"}, {"role": "assistant"}],
        ]
        service.db.list_chat_attachments.return_value = attachments or []
        service.ollama = Mock()
        service.retriever = Mock()
        service.retriever.search.return_value = []
        service.settings = Mock(max_context_chars=24000)
        return service

    def test_clarifying_question_is_saved_without_running_retrieval(self):
        service = AssistantService.__new__(AssistantService)
        service.db = Mock()
        service.db.messages.return_value = []
        service.db.list_chat_attachments.return_value = []
        service.ollama = Mock()
        service.ollama.interpret_question.return_value = {
            "intent": "Извлечение данных",
            "search_query": "параметры катализатора",
            "needs_clarification": True,
            "clarifying_question": "По какой партии нужны данные?",
        }
        service.retriever = Mock()
        service.settings = Mock()

        answer, sources, query = service.answer("conversation", "Дай данные по катализатору")

        self.assertEqual("По какой партии нужны данные?", answer)
        self.assertEqual([], sources)
        self.assertEqual("параметры катализатора", query)
        service.retriever.search.assert_not_called()
        self.assertEqual(2, service.db.add_message.call_count)
        service.db.rename_conversation.assert_called_once_with(
            "conversation", "Дай данные по катализатору"
        )

    def test_work_code_mode_skips_document_interpreter_and_retrieval(self):
        service = AssistantService.__new__(AssistantService)
        service.db = Mock()
        service.db.messages.side_effect = [[], [{"role": "user"}, {"role": "assistant"}]]
        service.db.list_chat_attachments.return_value = []
        service.ollama = Mock()
        service.ollama.answer.return_value = "```vba\nSub Test()\nEnd Sub\n```"
        service.retriever = Mock()
        service.settings = Mock(max_context_chars=24000)

        answer, sources, query = service.answer(
            "conversation",
            "Напиши макрос",
            strict=False,
            answer_mode="Рабочий код",
            use_rag=False,
        )

        self.assertIn("Sub Test", answer)
        self.assertEqual([], sources)
        self.assertEqual("Напиши макрос", query)
        service.ollama.interpret_question.assert_not_called()
        service.retriever.search.assert_not_called()

    def test_code_follow_up_is_discussed_without_regenerating_full_solution(self):
        history = [
            {"role": "user", "content": "Напиши макрос"},
            {"role": "assistant", "content": "```vba\nSub Test()\nEnd Sub\n```"},
        ]
        service = self._routed_service(history)
        service.ollama.interpret_question.return_value = {
            "intent": "Объяснение кода",
            "search_query": "язык и запуск предыдущего макроса",
            "needs_clarification": False,
            "clarifying_question": "",
            "response_kind": "conversation",
            "use_knowledge": False,
        }
        service.ollama.answer.return_value = "Это VBA; макрос запускается из Excel."

        answer, sources, _ = service.answer(
            "conversation",
            "Это на каком языке написано и как это использовать?",
            strict=False,
            model="general",
            code_model="coder",
            use_rag=None,
        )

        self.assertIn("VBA", answer)
        self.assertEqual([], sources)
        service.retriever.search.assert_not_called()
        self.assertEqual("Обсуждение кода", service.ollama.answer.call_args.kwargs["answer_mode"])
        self.assertEqual("coder", service.ollama.answer.call_args.kwargs["model"])

    def test_improvement_question_is_deterministically_discussed(self):
        history = [
            {"role": "assistant", "content": "```vba\nSub Test()\nEnd Sub\n```"},
        ]
        service = self._routed_service(history)
        service.ollama.interpret_question.return_value = {
            "intent": "Улучшение кода",
            "search_query": "Как можно усилить полученный код?",
            "needs_clarification": False,
            "clarifying_question": "",
            "response_kind": "conversation",
            "use_knowledge": False,
        }
        service.ollama.answer.return_value = "Сначала добавьте проверки и журналирование."

        service.answer(
            "conversation",
            "Как можно усилить полученный код?",
            strict=False,
            code_model="coder",
            use_rag=None,
        )

        self.assertEqual(
            "Обсуждение кода", service.ollama.answer.call_args.kwargs["answer_mode"]
        )

    def test_generation_failure_marks_saved_question_for_retry(self):
        service = AssistantService.__new__(AssistantService)
        service.db = Mock()
        service.db.messages.return_value = []
        service.db.add_message.return_value = 42
        service.db.list_chat_attachments.return_value = []
        service.ollama = Mock()
        service.ollama.interpret_question.side_effect = RuntimeError("model disconnected")
        service.retriever = Mock()
        service.settings = Mock(max_context_chars=24000)

        with self.assertRaisesRegex(RuntimeError, "model disconnected"):
            service.answer("conversation", "Продолжи", strict=False, use_rag=None)

        service.db.add_message.assert_called_once_with(
            "conversation", "user", "Продолжи", status="pending"
        )
        service.db.set_message_status.assert_called_once_with(
            42, "failed", "model disconnected"
        )

    def test_retry_reuses_unanswered_user_message_without_duplicate(self):
        pending = {
            "id": 29,
            "conversation_id": "conversation",
            "role": "user",
            "content": "Как улучшить код?",
            "status": "failed",
            "error": "connection lost",
        }
        service = AssistantService.__new__(AssistantService)
        service.db = Mock()
        service.db.messages.side_effect = [
            [pending],
            [pending, {"role": "assistant", "content": "Добавьте проверки."}],
        ]
        service.db.message.return_value = pending
        service.db.list_chat_attachments.return_value = []
        service.ollama = Mock()
        service.ollama.interpret_question.return_value = {
            "intent": "Улучшение кода",
            "search_query": "Как улучшить код?",
            "needs_clarification": False,
            "clarifying_question": "",
            "response_kind": "conversation",
            "use_knowledge": False,
        }
        service.ollama.answer.return_value = "Добавьте проверки."
        service.retriever = Mock()
        service.settings = Mock(max_context_chars=24000)

        answer, _, _ = service.answer(
            "conversation",
            "ignored",
            strict=False,
            use_rag=None,
            retry_message_id=29,
        )

        self.assertEqual("Добавьте проверки.", answer)
        self.assertEqual(1, service.db.add_message.call_count)
        self.assertEqual("assistant", service.db.add_message.call_args.args[1])
        self.assertEqual(
            [
                call(29, "pending"),
                call(29, "complete"),
            ],
            service.db.set_message_status.call_args_list,
        )

    def test_general_conversation_does_not_search_rag_in_auto_mode(self):
        service = self._routed_service([])
        service.ollama.interpret_question.return_value = {
            "intent": "Обычный разговор",
            "search_query": "объясни простыми словами",
            "needs_clarification": False,
            "clarifying_question": "",
            "response_kind": "conversation",
            "use_knowledge": False,
        }
        service.ollama.answer.return_value = "Простое объяснение."

        answer, _, _ = service.answer(
            "conversation", "Объясни простыми словами", strict=False, use_rag=None
        )

        self.assertEqual("Простое объяснение.", answer)
        service.retriever.search.assert_not_called()

    def test_old_table_attachment_does_not_hijack_unrelated_follow_up(self):
        attachment = {
            "filename": "signals.csv",
            "extracted_text": "Сводка набора данных: signals.csv",
        }
        service = self._routed_service([], [attachment])
        service.ollama.interpret_question.return_value = {
            "intent": "Обычный разговор",
            "search_query": "другой вопрос",
            "needs_clarification": False,
            "clarifying_question": "",
            "response_kind": "conversation",
            "use_knowledge": False,
        }
        service.ollama.answer.return_value = "Пожалуйста."

        _, sources, _ = service.answer(
            "conversation", "Спасибо, теперь другой вопрос", strict=False, use_rag=None
        )

        self.assertEqual([], sources)
        self.assertEqual([], service.ollama.answer.call_args.kwargs["attachments"])

    def test_structured_attachment_uses_analysis_and_can_combine_with_rag(self):
        attachment = {
            "filename": "signals.csv",
            "extracted_text": "Сводка набора данных: signals.csv\nСтрок данных: 100",
        }
        service = self._routed_service([], [attachment])
        service.ollama.interpret_question.return_value = {
            "intent": "Сопоставление данных",
            "search_query": "сравнить сигналы с нормативами",
            "needs_clarification": False,
            "clarifying_question": "",
            "response_kind": "analysis",
            "use_knowledge": True,
        }
        service.ollama.answer.return_value = "Сопоставление выполнено."

        _, sources, _ = service.answer(
            "conversation",
            "Сравни таблицу с нормативами из базы",
            strict=False,
            use_rag=None,
        )

        service.retriever.search.assert_called_once()
        self.assertEqual("Аналитический разбор", service.ollama.answer.call_args.kwargs["answer_mode"])
        self.assertIn("вычисленную сводку", service.ollama.answer.call_args.kwargs["custom_instruction"])
        self.assertEqual("signals.csv", sources[0]["filename"])

    def test_attachment_is_persistent_but_not_added_to_rag(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(data_dir=Path(directory))
            settings.ensure_directories()
            service = AssistantService.__new__(AssistantService)
            service.settings = settings
            service.db = Database(settings.db_path)
            conversation = service.db.create_conversation()

            content = "Рабочее давление 5 МПа".encode("utf-8")
            attachment, created = service.attach_to_conversation(
                conversation, "temporary.txt", content
            )
            duplicate, duplicate_created = service.attach_to_conversation(
                conversation, "temporary.txt", content
            )

            self.assertTrue(created)
            self.assertFalse(duplicate_created)
            self.assertEqual(attachment["id"], duplicate["id"])
            self.assertIn("Рабочее давление 5 МПа", attachment["extracted_text"])
            self.assertEqual([], service.db.list_documents())
            self.assertEqual([], service.db.all_chunks())
            self.assertTrue(Path(attachment["stored_path"]).exists())

            service.delete_conversation(conversation)
            self.assertFalse(Path(attachment["stored_path"]).exists())

    def test_long_attachment_keeps_sections_relevant_to_question(self):
        text = "\n\n".join(
            ["[стр. 1]\nОбщее описание установки " + "x" * 3000]
            + ["[стр. 2]\nДавление D2B составляет 5 МПа"]
            + ["[стр. 3]\nДополнительные сведения " + "y" * 3000]
        )

        selected = AssistantService._select_attachment_text(text, "давление D2B", 1200)

        self.assertIn("Давление D2B составляет 5 МПа", selected)
        self.assertLessEqual(len(selected), 1200)

    def test_long_structured_attachment_always_keeps_dataset_summary(self):
        text = "\n\n".join(
            ["[signals.csv, сводка]\nСводка набора данных: signals.csv\nСтрок данных: 10000"]
            + [f"[строки {index}]\nПараметр={index} " + "x" * 500 for index in range(20)]
        )

        selected = AssistantService._select_attachment_text(text, "найди отклонения", 1200)

        self.assertIn("Строк данных: 10000", selected)
        self.assertLessEqual(len(selected), 1200)


if __name__ == "__main__":
    unittest.main()
