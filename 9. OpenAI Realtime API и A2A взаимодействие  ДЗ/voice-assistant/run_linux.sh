#!/usr/bin/env bash
# запуск voice-assistant на Linux / macOS
# перед первым запуском выполните ./install_linux.sh

set -e

cd "$(dirname "$0")"
source .venv/bin/activate

# открываем браузер с небольшой задержкой, чтобы сервер успел подняться
if command -v xdg-open >/dev/null 2>&1
then
    (sleep 3 && xdg-open http://localhost:8000/) &
elif command -v open >/dev/null 2>&1
then
    (sleep 3 && open http://localhost:8000/) &
fi

python main.py
