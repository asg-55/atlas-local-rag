# Atlas Desktop — отдельный прототип Windows-сборки

`atlas_launcher.py` — минимальный супервизор будущей Windows-сборки. Он не
устанавливает и не скрывает Docker или Ollama. Desktop-сборка не заменяет
Docker-редакцию Atlas: `compose.yaml`, `.env`, проектные `data/` и `model_cache/`
остаются самостоятельным рабочим контуром.

Ожидаемая структура установленного комплекта:

```text
Atlas\
  app\app.py
  app\rag_assistant\...
  runtime\python\python.exe
  runtime\llama\cpu\llama-server.exe
  runtime\llama\vulkan\llama-server.exe   # опциональное ускорение

%LOCALAPPDATA%\Atlas\
  data\
  models\chat.gguf
  models\huggingface\...
  models\easyocr\...
  logs\
```

Проверка неполного или собранного комплекта ничего не запускает и не создаёт:

```powershell
python desktop\atlas_launcher.py --check
```

При обычном запуске супервизор выбирает свободные локальные порты. Если в
комплекте есть Vulkan backend, сначала используется GPU; при ошибке загрузки
драйвера или модели выполняется автоматический откат на обязательный CPU
backend. Затем запускается Streamlit и проверяется `/_stcore/health`. Оба
процесса слушают только `127.0.0.1`. API llama-server закрыт случайным ключом на
каждый запуск. Для Hugging Face принудительно включён offline-режим, а
изменяемые данные находятся вне каталога приложения.

## Закреплённые компоненты прототипа

[`components.json`](components.json) фиксирует версии, URL, размер и SHA-256:

- Qwen3.5-4B Q4_K_M, 2 740 937 888 байт, контекст Atlas 8192;
- llama.cpp `b10456` Windows x64 CPU — обязательный backend;
- llama.cpp `b10456` Windows x64 Vulkan — опциональный GPU backend для NVIDIA,
  AMD и Intel при наличии рабочего Vulkan-драйвера.

Компоненты готовятся только в исключённый из Git staging-каталог. Например,
из уже собранного CI target:

```powershell
docker build --target test -t atlas-ci:desktop .
docker run --rm -v "${PWD}:/src" -w /src atlas-ci:desktop `
  python desktop/prepare_runtime.py --destination model_cache/desktop
```

Загрузчик повторно использует только файл с правильными размером и SHA-256,
проверяет архив до распаковки и запрещает выход файлов за staging-каталог.

## Фактически проверено

На Windows оба официальных `llama-server.exe` запускаются, точный GGUF проходит
SHA-256 и формирует ответ через OpenAI-совместимый API как на CPU, так и на
Vulkan. Отдельно обнаружено, что GGUF blob из Ollama не совместим с актуальным
upstream llama.cpp по metadata RoPE, поэтому Desktop никогда не переиспользует
Ollama blob и поставляет собственный проверенный model pack.

До пилотной поставки всё ещё нужны упакованный Python и Windows wheels,
ML/OCR-кэши, LibreOffice/FFmpeg, Windows Job Object, защита от двойного запуска,
ротация технических логов и подписанный установочный manifest. На машине лишь с
8 ГБ ОЗУ пока нельзя обещать одновременную CPU-работу LLM, PyTorch RAG и OCR без
выгрузки неиспользуемых компонентов; это следующий обязательный memory-тест.
