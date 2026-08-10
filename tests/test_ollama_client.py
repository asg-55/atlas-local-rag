import json
import unittest
from unittest.mock import Mock, patch

import requests

from rag_assistant.ollama_client import OllamaClient


class OllamaClientTests(unittest.TestCase):
    @patch("rag_assistant.ollama_client.requests.get")
    def test_capabilities_are_optional_when_ollama_is_offline(self, get):
        get.side_effect = requests.ConnectionError("offline")
        client = OllamaClient("http://ollama", "qwen3.5:9b")

        self.assertEqual(set(), client.capabilities("qwen3.5:9b"))

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
                    "response_kind": "knowledge",
                    "use_knowledge": True,
                },
                ensure_ascii=False,
            )
        )

        result = client.interpret_question("Дай данные по катализатору", [])

        self.assertTrue(result["needs_clarification"])
        self.assertEqual("параметры катализатора", result["search_query"])
        self.assertEqual("По какой партии нужны данные?", result["clarifying_question"])
        self.assertEqual("knowledge", result["response_kind"])
        self.assertTrue(result["use_knowledge"])
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

    def test_work_code_mode_requests_complete_copyable_solution(self):
        client = OllamaClient("http://ollama", "qwen3.5:9b")
        client.generate = Mock(
            side_effect=[
                "```python\nprint('draft')\n```",
                "```python\nprint('reviewed')\n```",
            ]
        )

        answer = client.answer(
            "Напиши скрипт для CSV",
            [],
            [],
            strict=False,
            answer_mode="Рабочий код",
        )

        self.assertIn("reviewed", answer)
        self.assertEqual(2, client.generate.call_count)
        prompt = client.generate.call_args_list[0].args[0]
        review_prompt = client.generate.call_args_list[1].args[0]
        self.assertIn("полный самодостаточный код", prompt)
        self.assertIn("VBA, Python или C#", prompt)
        self.assertIn("сначала читай в `Variant`", prompt)
        self.assertIn("ошибки типов", prompt)
        self.assertNotIn("После каждого существенного утверждения", prompt)
        self.assertIn("последнюю строку бери из обрабатываемого столбца", review_prompt)
        self.assertIn("затем `IsNumeric`, затем `CDbl`", review_prompt)
        self.assertIn("Не используй внешний блок `markdown`", review_prompt)
        self.assertFalse(client.generate.call_args_list[1].kwargs["think"])

    def test_code_follow_up_answers_once_without_full_regeneration(self):
        client = OllamaClient("http://ollama", "qwen3.5:9b")
        client.generate = Mock(return_value="Переменная хранит номер текущей строки.")
        history = [
            {"role": "assistant", "content": "```python\nfor row in rows:\n    print(row)\n```"}
        ]

        answer = client.answer(
            "Что делает переменная row?",
            [],
            history,
            strict=False,
            answer_mode="Обсуждение кода",
        )

        self.assertIn("номер", answer)
        self.assertEqual(1, client.generate.call_count)
        prompt = client.generate.call_args.args[0]
        self.assertIn("for row in rows", prompt)
        self.assertIn("Не генерируй заново полный код", prompt)

    def test_code_improvement_advice_explicitly_forbids_rewritten_code(self):
        client = OllamaClient("http://ollama", "qwen3.5:9b")
        client.generate = Mock(return_value="Сначала добавьте проверку конфликтов имён.")

        client.answer(
            "Как можно усилить полученный код?",
            [],
            [{"role": "assistant", "content": "```vba\nSub MoveFiles()\nEnd Sub\n```"}],
            strict=False,
            answer_mode="Обсуждение кода",
        )

        prompt = client.generate.call_args.args[0]
        self.assertIn("предложи выбрать вариант", prompt)
        self.assertIn("не показывай полный или изменённый код", prompt)

    def test_general_conversation_does_not_invent_document_sources(self):
        client = OllamaClient("http://ollama", "qwen3.5:9b")
        client.generate = Mock(return_value="Короткое объяснение")

        client.answer(
            "Объясни простыми словами",
            [],
            [],
            strict=False,
            answer_mode="Краткий ответ",
        )

        prompt = client.generate.call_args.args[0]
        self.assertIn("локальный рабочий ассистент Atlas", prompt)
        self.assertIn("без выдуманных ссылок на документы", prompt)
        self.assertNotIn("После каждого существенного утверждения", prompt)

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
