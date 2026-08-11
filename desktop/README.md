# Прототип переносимого запуска Atlas

`atlas_launcher.py` — минимальный супервизор будущей Windows-сборки. Он не
устанавливает и не скрывает Docker или Ollama. Ожидаемая структура комплекта:

```text
Atlas\
  app\app.py
  app\rag_assistant\...
  runtime\python\python.exe
  runtime\llama\llama-server.exe

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

При обычном запуске супервизор выбирает свободные локальные порты, запускает
`llama-server`, ждёт `/health`, затем запускает Streamlit и ждёт
`/_stcore/health`. Оба процесса слушают только `127.0.0.1`. Для Hugging Face
принудительно включён offline-режим, а изменяемые данные находятся вне каталога
приложения.

Прототип намеренно запускает llama.cpp на CPU. Автоматическое GPU-ускорение
будет добавлено только после проверки отдельных Windows backend-пакетов и
обязательного CPU fallback. До пилотной поставки также нужны Windows Job Object,
защита от двойного запуска, ротация технических логов и подписанный manifest с
SHA-256 компонентов.
