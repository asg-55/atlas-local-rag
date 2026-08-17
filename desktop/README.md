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

- официальный Windows embeddable Python 3.11.9;
- build-only pip 26.2.1, удаляемый из готового runtime;
- Qwen3.5-4B Q4_K_M, 2 740 937 888 байт, контекст Atlas 8192;
- llama.cpp `b10456` Windows x64 CPU — обязательный backend;
- llama.cpp `b10456` Windows x64 Vulkan — опциональный GPU backend для NVIDIA,
  AMD и Intel при наличии рабочего Vulkan-драйвера.

Python-зависимости Desktop отделены от Docker-зависимостей. Прямые версии
перечислены в `requirements-windows.in`, а `requirements-windows.lock.json`
фиксирует 83 конкретных Windows wheel с URL и SHA-256. Builder допускает только
бинарные wheels, сначала создаёт проверенный возобновляемый wheelhouse, затем
устанавливает зависимости полностью офлайн и удаляет pip.

Полная подготовка Python backend выполняется в исключённый из Git staging:

```powershell
./desktop/build_python_runtime.ps1 -Destination model_cache/desktop
```

LLM-компоненты можно подготовить отдельно через `prepare_runtime.py`. Загрузчики
повторно используют только файлы с правильными размером и SHA-256, проверяют
архивы до распаковки и запрещают выход файлов за staging-каталог.

## Фактически проверено

На Windows оба официальных `llama-server.exe` запускаются, точный GGUF проходит
SHA-256 и формирует ответ через OpenAI-совместимый API как на CPU, так и на
Vulkan. Отдельно обнаружено, что GGUF blob из Ollama не совместим с актуальным
upstream llama.cpp по metadata RoPE, поэтому Desktop никогда не переиспользует
Ollama blob и поставляет собственный проверенный model pack.

Собран и проверен официальный embeddable Python 3.11.9 с Windows wheels.
Распакованный runtime без pip занимает 2 553 169 007 байт; сжатый wheelhouse —
540 761 719 байт. Нативная проверка подтвердила Python 3.11.9, SQLite
3.45.1/FTS5, FAISS, PyTorch/torchvision CPU, Sentence Transformers, EasyOCR,
faster-whisper, OpenCV, CTranslate2, Streamlit, Office/PDF и cryptography.
Полный supervisor успешно поднял Vulkan llama-server и упакованный Streamlit,
после чего `/_stcore/health` вернул `ok`.

Idle-замер полного supervisor с Vulkan составил около 3,04 ГБ working set и
4,11 ГБ private bytes на тестовой Windows-машине; основной потребитель —
llama-server. Для однопользовательского Desktop число параллельных слотов
снижено с четырёх до одного: контекст 8192 сохраняется, private bytes
llama-server уменьшились примерно с 4,05 до 3,82 ГБ. `--no-host` измеримого
выигрыша не дал и не используется.

Launcher использует Windows Job Object с `KILL_ON_JOB_CLOSE`; жёсткий тест
подтвердил, что принудительное завершение родителя автоматически закрывает всё
дерево llama-server/Streamlit.

До пилотной поставки всё ещё нужны ML/OCR/Whisper-кэши, LibreOffice/FFmpeg,
защита от двойного запуска, ротация технических логов и подписанный
установочный manifest. На машине лишь с
8 ГБ ОЗУ пока нельзя обещать одновременную CPU-работу LLM, PyTorch RAG и OCR без
выгрузки неиспользуемых компонентов; это следующий обязательный memory-тест.
