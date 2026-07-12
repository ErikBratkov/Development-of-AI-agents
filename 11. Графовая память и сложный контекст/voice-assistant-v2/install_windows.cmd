@echo off
rem установка voice-assistant на Windows 11
rem запуск без аргументов - полный вариант: локальные STT и TTS плюс
rem база знаний в Neo4j (группа rag с эмбеддингами)
rem запуск с аргументом min - минимальный вариант (только текстовый режим):
rem   install_windows.cmd min
rem в обоих вариантах ставятся dev зависимости (pytest для unit тестов)
rem важно: в пути проекта не должно быть кириллических символов
chcp 65001 >nul

cd /d "%~dp0"

echo Создание виртуального окружения .venv
py -3.13 -m venv .venv
if errorlevel 1 goto fail
call .venv\Scripts\activate

if "%1"=="min" (
    echo Установка зависимостей - минимальный вариант
    pip install -e ".[dev]"
) else (
    echo Установка зависимостей - полный вариант с audio и rag
    pip install -e ".[audio,rag,dev]"
)
if errorlevel 1 goto fail

rem .env не трогаем, если он уже есть
if not exist .env (
    copy .env.example .env
    echo Создан .env - впишите в него свой OPENROUTER_API_KEY
)

if not "%1"=="min" (
    echo Скачивание голоса для Piper
    if not exist models mkdir models
    python -m piper.download_voices ru_RU-irina-medium --data-dir models
    echo Путь к .onnx файлу укажите в TTS_VOICE в .env
)

echo Установка завершена, для запуска используйте run_windows.cmd
if not "%1"=="min" (
    echo База знаний наполнится при запуске сама, нужен работающий docker
)
goto end

:fail
echo Установка прервана из-за ошибки, смотрите сообщения выше

:end
pause
