import json
import unittest
from unittest.mock import Mock, patch

from rag_assistant.ollama_client import OllamaClient


class OllamaClientTests(unittest.TestCase):
    @patch("rag_assistant.ollama_client.requests.post")
    def test_reads_model_context_length(self, post):
        response = Mock()
        response.json.return_value = {
            "model_info": {"general.architecture": "qwen35", "qwen35.context_length": 262144}
        }
        post.return_value = response

        client = OllamaClient("http://ollama", "qwen3.5:9b")

        self.assertEqual(262144, client.context_length())
        self.assertEqual(262144, client.context_length())
        self.assertEqual(1, post.call_count)

    def test_interprets_vague_question_and_requests_clarification(self):
        client = OllamaClient("http://ollama", "qwen3.5:9b")
        client.generate = Mock(
            return_value=json.dumps(
                {
                    "intent": "Извлечение данных",
                    "search_query": "параметры катализатора",
                    "needs_clarification": True,
                    "clarifying_question": "По какой партии нужны данные?",
                },
                ensure_ascii=False,
            )
        )

        result = client.interpret_question("Дай данные по катализатору", [])

        self.assertTrue(result["needs_clarification"])
        self.assertEqual("параметры катализатора", result["search_query"])
        self.assertEqual("По какой партии нужны данные?", result["clarifying_question"])
        self.assertTrue(client.generate.call_args.kwargs["json_output"])

    def test_interpreter_does_not_treat_string_false_as_true(self):
        client = OllamaClient("http://ollama", "qwen3.5:9b")
        client.generate = Mock(
            return_value=json.dumps(
                {
                    "intent": "Поиск значения",
                    "search_query": "давление в D2B",
                    "needs_clarification": "false",
                    "clarifying_question": "",
                },
                ensure_ascii=False,
            )
        )

        result = client.interpret_question("Какое давление в D2B?", [])

        self.assertFalse(result["needs_clarification"])
        self.assertEqual("давление в D2B", result["search_query"])

    @patch("rag_assistant.ollama_client.requests.post")
    def test_retries_without_thinking_when_final_answer_is_empty(self, post):
        thinking_only = Mock()
        thinking_only.json.return_value = {"response": "", "thinking": "long reasoning"}
        final = Mock()
        final.json.return_value = {"response": "Готовый ответ", "thinking": ""}
        post.side_effect = [thinking_only, final]

        client = OllamaClient("http://ollama", "qwen3.5:9b")
        answer = client.generate("prompt", think=True, num_predict=512)

        self.assertEqual("Готовый ответ", answer)
        self.assertTrue(post.call_args_list[0].kwargs["json"]["think"])
        self.assertFalse(post.call_args_list[1].kwargs["json"]["think"])

    @patch("rag_assistant.ollama_client.requests.post")
    def test_empty_final_answer_is_an_error(self, post):
        response = Mock()
        response.json.return_value = {"response": "", "thinking": ""}
        post.return_value = response

        client = OllamaClient("http://ollama", "qwen3.5:9b")
        with self.assertRaisesRegex(RuntimeError, "не сформировала"):
            client.generate("prompt", think=False)


if __name__ == "__main__":
    unittest.main()
