import unittest
from unittest.mock import Mock, patch

from rag_assistant.ollama_client import OllamaClient


class OllamaClientTests(unittest.TestCase):
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
