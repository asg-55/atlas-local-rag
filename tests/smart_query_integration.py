"""Manual smoke test for the live Ollama query interpreter."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rag_assistant.ollama_client import OllamaClient


def main() -> None:
    base_url = os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
    model = os.getenv("CHAT_MODEL", "qwen3.5:9b")
    client = OllamaClient(base_url, model, timeout=300)

    context_length = client.context_length(model)
    assert context_length >= 32768, context_length

    clear = client.interpret_question("Какое давление указано для D2B?", [], model=model)
    assert clear["search_query"], clear
    assert not clear["needs_clarification"], clear

    vague = client.interpret_question("Дай данные по катализатору", [], model=model)
    assert vague["search_query"], vague
    assert vague["needs_clarification"], vague
    assert vague["clarifying_question"], vague

    print({"model": model, "context_length": context_length, "clear": clear, "vague": vague})


if __name__ == "__main__":
    main()
