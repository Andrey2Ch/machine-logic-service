#!/bin/bash

# Установить порт по умолчанию, если PORT не задан
PORT=${PORT:-8000}

# Запустить uvicorn с правильным портом
exec uvicorn src.main:app --host 0.0.0.0 --port $PORT 