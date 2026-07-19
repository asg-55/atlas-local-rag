@echo off
setlocal
title Локальный RAG-ассистент
cd /d "%~dp0"

docker info >nul 2>&1
if errorlevel 1 (
    echo Docker Desktop не запущен или не установлен.
    echo Установите Docker Desktop и повторите запуск.
    pause
    exit /b 1
)

echo Запуск ассистента в Docker...
docker compose up -d --build
if errorlevel 1 (
    echo Не удалось собрать или запустить контейнер.
    pause
    exit /b 1
)

echo.
echo Ассистент доступен: http://localhost:8501
start "" http://localhost:8501
docker compose logs -f assistant
