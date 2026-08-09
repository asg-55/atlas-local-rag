from __future__ import annotations

import requests

from .ollama_client import OllamaClient


class LlamaCppClient(OllamaClient):
    """llama-server transport with the existing Atlas prompt workflow."""

    def models(self) -> list[str]:
        response = requests.get(f"{self.base_url}/v1/models", timeout=5)
        response.raise_for_status()
        names = [
            item.get("id")
            for item in response.json().get("data", [])
            if isinstance(item, dict) and item.get("id")
        ]
        return names or [self.model]

    def capabilities(self, model: str) -> set[str]:
        # llama-server exposes one loaded model. Atlas does not need optional
        # Ollama capability metadata to generate a valid answer.
        return set()

    def context_length(self, model: str | None = None) -> int:
        selected = model or self.model
        if selected in self._context_lengths:
            return self._context_lengths[selected]
        try:
            response = requests.get(f"{self.base_url}/props", timeout=5)
            response.raise_for_status()
            payload = response.json()
            defaults = payload.get("default_generation_settings") or {}
            length = int(defaults.get("n_ctx") or payload.get("n_ctx") or 32768)
        except (requests.RequestException, TypeError, ValueError):
            length = 32768
        self._context_lengths[selected] = length
        return length

    def health(self, model: str | None = None) -> tuple[bool, str]:
        try:
            response = requests.get(f"{self.base_url}/health", timeout=5)
            response.raise_for_status()
            return True, "Встроенная модель подключена"
        except requests.RequestException as exc:
            return False, str(exc)

    def generate(
        self,
        prompt: str,
        temperature: float = 0.2,
        num_predict: int = 2200,
        model: str | None = None,
        top_p: float = 0.9,
        num_ctx: int = 16384,
        think: bool = False,
        json_output: bool = False,
    ) -> str:
        payload = {
            "model": model or self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": num_predict,
            "top_p": top_p,
            "stream": False,
            "chat_template_kwargs": {"enable_thinking": think},
        }
        if json_output:
            payload["response_format"] = {"type": "json_object"}
        response = requests.post(
            f"{self.base_url}/v1/chat/completions",
            json=payload,
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()
        choices = data.get("choices") or []
        message = choices[0].get("message", {}) if choices else {}
        answer = str(message.get("content") or "").strip()
        if not answer:
            raise RuntimeError(
                "Модель не сформировала финальный ответ. "
                "Увеличьте лимит токенов или отключите рассуждение."
            )
        return answer
