import unittest
from unittest.mock import Mock

from rag_assistant.service import AssistantService


class AssistantServiceTests(unittest.TestCase):
    def test_clarifying_question_is_saved_without_running_retrieval(self):
        service = AssistantService.__new__(AssistantService)
        service.db = Mock()
        service.db.messages.return_value = []
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


if __name__ == "__main__":
    unittest.main()
