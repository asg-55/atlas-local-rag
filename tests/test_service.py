import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from rag_assistant.config import Settings
from rag_assistant.database import Database
from rag_assistant.service import AssistantService


class AssistantServiceTests(unittest.TestCase):
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
