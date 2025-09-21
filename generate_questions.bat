@echo off
echo 🚀 Генерация вопросов из captured SQL queries...
echo.

cd /d "%~dp0"

REM Активируем виртуальное окружение, если есть
if exist "venv\Scripts\activate.bat" (
    echo ✅ Активируем venv...
    call venv\Scripts\activate.bat
) else if exist ".venv\Scripts\activate.bat" (
    echo ✅ Активируем .venv...
    call .venv\Scripts\activate.bat
) else (
    echo ⚠️  Виртуальное окружение не найдено, используем системный Python
)

echo.
echo 🔄 Запускаем генератор вопросов...
python src\text2sql\scripts\generate_questions_from_captured.py

echo.
echo ✨ Готово! Проверьте результаты на странице /sql/capture
pause
