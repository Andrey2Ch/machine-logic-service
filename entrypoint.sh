#!/bin/bash

# Установить порт по умолчанию, если PORT не задан
PORT=${PORT:-8000}

# Создать папку для чертежей если не существует и дать права
mkdir -p /app/drawings
chmod 777 /app/drawings

# Запустить uvicorn с несколькими workers для параллельной обработки
exec uvicorn src.main:app --host 0.0.0.0 --port $PORT --workers 4 