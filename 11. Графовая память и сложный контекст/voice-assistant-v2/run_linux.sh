#!/usr/bin/env bash
# запуск voice-assistant на Linux / macOS
# перед первым запуском выполните ./install_linux.sh

set -e

cd "$(dirname "$0")"
source .venv/bin/activate

# --wait ждет готовности Neo4j по healthcheck из docker-compose.yml,
# иначе сидинг стартует раньше, чем база начнет принимать подключения.
# Отказ docker не блокирует запуск - сервис умеет работать и без базы
if docker compose up -d --wait
then
    python seed_knowledge.py \
        || echo "Наполнение базы не удалось, сервис стартует без нее"
else
    echo "Neo4j не поднялся, база знаний будет недоступна"
fi

# открываем браузер с небольшой задержкой, чтобы сервер успел подняться
if command -v xdg-open >/dev/null 2>&1
then
    (sleep 3 && xdg-open http://localhost:8000/) &
elif command -v open >/dev/null 2>&1
then
    (sleep 3 && open http://localhost:8000/) &
fi

# контейнер Neo4j продолжает работать, остановка - ./stop_linux.sh
python main.py
