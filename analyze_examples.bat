@echo off
echo 🚀 Запуск анализа и дедупликации captured SQL запросов
echo ========================================================

cd /d "%~dp0"
python src\text2sql\scripts\analyze_and_deduplicate.py

echo.
echo ✅ Анализ завершен!
pause
