#!/usr/bin/env bash
# установка voice-assistant на Linux / macOS
# запуск без аргументов - полный вариант: локальные STT и TTS плюс
# база знаний в Neo4j (группа rag с эмбеддингами)
# запуск с аргументом min - минимальный вариант (только текстовый режим):
#   ./install_linux.sh min
# в обоих вариантах ставятся dev зависимости (pytest для unit тестов)

set -e

# работаем из каталога, где лежит сам скрипт
cd "$(dirname "$0")"

echo "Создание виртуального окружения .venv"
python3 -m venv .venv
source .venv/bin/activate

if [ "$1" = "min" ]
then
    echo "Установка зависимостей (минимальный вариант)"
    pip install -e ".[dev]"
else
    echo "Установка зависимостей (полный вариант с audio и rag)"
    pip install -e ".[audio,rag,dev]"
fi

# .env не трогаем, если он уже есть
if [ ! -f .env ]
then
    cp .env.example .env
    echo "Создан .env - впишите в него свой OPENROUTER_API_KEY"
fi

if [ "$1" != "min" ]
then
    echo "Скачивание голоса для Piper"
    mkdir -p models
    python -m piper.download_voices ru_RU-irina-medium --data-dir models
    echo "Путь к .onnx файлу укажите в TTS_VOICE в .env"
fi

echo "Установка завершена, для запуска используйте ./run_linux.sh"
if [ "$1" != "min" ]
then
    echo "База знаний наполнится при запуске сама, нужен работающий docker"
fi
