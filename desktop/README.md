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
  desktop\atlas_launcher.py
  models\chat.gguf
  models\huggingface\...
  models\easyocr\...
  runtime\python\python.exe
  runtime\llama\cpu\llama-server.exe
  runtime\llama\vulkan\llama-server.exe   # опциональное ускорение
  runtime\libreoffice\...

%LOCALAPPDATA%\Atlas\
  data\
  models\          # место для будущих отдельно установленных model packs
  logs\
```

Проверка неполного или собранного комплекта ничего не запускает и не создаёт:

```powershell
python desktop\atlas_launcher.py --check
```

Расширенная диагностика также ничего не запускает. Она показывает комплектность,
свободное место, физическую память и обнаруженные Vulkan-устройства:

```powershell
python desktop\atlas_launcher.py --diagnostics
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

После Python runtime отдельный builder готовит офлайн-функции анализа:

```powershell
./desktop/build_offline_assets.ps1 -Destination model_cache/desktop
```

Он административно распаковывает закреплённый LibreOffice 26.2.5 и готовит
только необходимые файлы трёх model pack по точным Hugging Face revisions:
`multilingual-e5-base`, `mmarco-mMiniLMv2-L12-H384-v1` и
`faster-whisper-small`. Два файла EasyOCR проверяются SHA-256 архивов и
upstream MD5 распакованных весов. Частичные загрузки сохраняются в отдельном
cache и возобновляются; download cache в дистрибутив не входит.

Отдельный FFmpeg CLI не добавляется: Atlas его не запускает, а
`faster-whisper` декодирует аудио через уже упакованный PyAV.

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

Повторный запуск Atlas не создаёт второй комплект процессов: Windows mutex
привязан к каталогу пользовательского состояния, а сохранённый loopback URL
открывается повторно. Технические логи ограничены 5 МБ и тремя резервными
копиями на каждый процесс. В `runtime.json` находятся только PID, локальный URL,
тип backend и время запуска; файл удаляется при штатной остановке.

Офлайн-набор моделей занимает 2 211 722 234 байта, административный образ
LibreOffice — 1 594 984 948 байт. Вместе с Python, Qwen и llama.cpp полный
установленный каталог ожидаемо занимает около 9,2 ГБ; размер сжатого установщика
нужно измерить после выбора его формата.

Нативные inference-пробы подтвердили embedding 768, reranker, Whisper на WAV и
EasyOCR на контрольном изображении. Пиковые private bytes Python во время
embedding достигли 4,42 ГБ. Поэтому Desktop запускает llama-server с
`--sleep-idle-seconds 1`: проверенный Vulkan backend после секунды простоя
снизился примерно с 3,94 ГБ до 95 МБ private memory и автоматически загрузился
на следующем запросе. Это устраняет постоянное сложение памяти Qwen и тяжёлого
RAG/OCR; после медиа-разбора и операций embeddings/reranker Desktop также
очищает модельные cache. Docker-профиль этого поведения не включает.

## Прототип установочного комплекта

[`installer/atlas-desktop.iss`](installer/atlas-desktop.iss) описывает per-user
установку Inno Setup 7 x64 в `%LOCALAPPDATA%\Programs\Atlas`. Администратор,
Docker, Ollama и системный Python не нужны. Изменяемые данные остаются в
`%LOCALAPPDATA%\Atlas` и намеренно не входят в правила удаления.

Сначала все компоненты готовятся в одном staging. Каталог `model_cache/desktop`
может содержать только базовый runtime и сам по себе не считается полным:

```powershell
python desktop/prepare_runtime.py --destination desktop/staging/components
./desktop/build_python_runtime.ps1 -Destination desktop/staging/components
./desktop/build_offline_assets.ps1 -Destination desktop/staging/components
```

Затем из полного проверенного component staging создаётся чистый payload. Скрипт требует
чистое рабочее дерево, записывает commit исходников и не переносит `downloads`,
`validation`, `data`, `.env` или рабочие документы:

```powershell
./desktop/prepare_installer_payload.ps1 `
  -BasePayload desktop/staging/components `
  -Destination desktop/staging/Atlas
```

В корне payload создаётся `payload-manifest.json` с относительным путём,
размером и SHA-256 каждого файла. Builder проверяет полный manifest перед сухой
проверкой и перед компиляцией:

```powershell
./desktop/build_installer.ps1 `
  -SourceDirectory desktop/staging/Atlas `
  -Version 0.1.0 `
  -ValidateOnly
```

Сборка с закреплённым Inno Setup 7.0.2 x64:

```powershell
./desktop/build_installer.ps1 `
  -SourceDirectory desktop/staging/Atlas `
  -Version 0.1.0
```

Так как payload больше 4,2 ГБ, результат является одним установочным комплектом:
небольшой `.exe`, пронумерованные блоки `.bin` не более 2 ГБ и
`SHA256SUMS.txt`. Каталоги build-cache `downloads` и `validation` не включаются.
Версия, официальный URL, размер и SHA-256 установщика compiler закреплены в
[`installer-tools.json`](installer-tools.json). Для публичной поставки ещё нужны
Authenticode-подпись EXE и внешняя подпись release manifest.

## Фактическая сборка 0.1.0

Первый полный установочный комплект собран Inno Setup 7.0.2 из payload commit
`db301f7`. Исходный payload содержит 61 421 файл и занимает 9 251 249 969 байт.
Компиляция `lzma2/normal` заняла около 30 минут и дала комплект размером
5 003 291 206 байт (4,66 GiB): EXE, три BIN-блока и `SHA256SUMS.txt`.

Комплект установлен в отдельный тестовый каталог, после чего установленный
launcher подтвердил все обязательные компоненты. Полный supervisor поднял
Vulkan llama-server и Streamlit, health вернул `ok`, а второй запуск не создал
дубликат процессов. Штатный uninstaller удалил тестовый каталог, ярлык и запись
HKCU без перезагрузки. `%LOCALAPPDATA%\Atlas` в этой проверке не создавался.

Это рабочий локальный прототип установщика, но не публичный релиз: EXE имеет
статус `NotSigned`, а проверка на чистой Windows и физическом ПК с 8 ГБ ОЗУ ещё
не выполнена.

Искусственная симуляция 8 ГБ ОЗУ исключена: она не доказывает работу при реальном
давлении памяти. Сейчас диагностика и запуск проверены на машине с 32 ГБ ОЗУ.
Физический ПК с 8 ГБ остаётся отдельной приёмочной проверкой; до неё нельзя
обещать комфортный CPU-only режим на такой конфигурации.
