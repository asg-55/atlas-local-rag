import unittest
from unittest.mock import Mock, patch

import requests

from rag_assistant.llama_cpp_client import LlamaCppClient


class LlamaCppClientTests(unittest.TestCase):
    @patch("rag_assistant.llama_cpp_client.requests.get")
    def test_reads_loaded_model_and_context(self, get):
        models = Mock()
        models.json.return_value = {"data": [{"id": "atlas-4b-q4"}]}
        props = Mock()
        props.json.return_value = {"default_generation_settings": {"n_ctx": 16384}}
        get.side_effect = [models, props]

        client = LlamaCppClient("http://llama", "atlas-4b-q4")

        self.assertEqual(["atlas-4b-q4"], client.models())
        self.assertEqual(16384, client.context_length())
        self.assertEqual(16384, client.context_length())
        self.assertEqual(2, get.call_count)

    @patch("rag_assistant.llama_cpp_client.requests.get")
    def test_context_has_safe_offline_fallback(self, get):
        get.side_effect = requests.ConnectionError("offline")
        client = LlamaCppClient("http://llama", "atlas-4b-q4")

        self.assertEqual(32768, client.context_length())

    @patch("rag_assistant.llama_cpp_client.requests.post")
    def test_generates_with_openai_compatible_endpoint(self, post):
        response = Mock()
        response.json.return_value = {
            "choices": [{"message": {"content": "Локальный ответ"}}]
        }
        post.return_value = response
        client = LlamaCppClient("http://llama", "atlas-4b-q4")

        answer = client.generate(
            "prompt", num_predict=512, think=True, json_output=True
        )

        self.assertEqual("Локальный ответ", answer)
        self.assertEqual(
            "http://llama/v1/chat/completions", post.call_args.args[0]
        )
        payload = post.call_args.kwargs["json"]
        self.assertEqual(512, payload["max_tokens"])
        self.assertTrue(payload["chat_template_kwargs"]["enable_thinking"])
        self.assertEqual({"type": "json_object"}, payload["response_format"])

    @patch("rag_assistant.llama_cpp_client.requests.post")
    def test_empty_answer_is_reported(self, post):
        response = Mock()
        response.json.return_value = {"choices": [{"message": {"content": ""}}]}
        post.return_value = response
        client = LlamaCppClient("http://llama", "atlas-4b-q4")

        with self.assertRaisesRegex(RuntimeError, "не сформировала"):
            client.generate("prompt")


if __name__ == "__main__":
    unittest.main()
