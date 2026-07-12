@echo off
rem запуск voice-assistant на Windows 11
rem перед первым запуском выполните install_windows.cmd
chcp 65001 >nul

cd /d "%~dp0"
call .venv\Scripts\activate

rem --wait ждет готовности Neo4j по healthcheck из docker-compose.yml,
rem иначе сидинг стартует раньше, чем база начнет принимать подключения
call docker compose up -d --wait
if errorlevel 1 (
    echo Neo4j не поднялся, база знаний будет недоступна
) else (
    python seed_knowledge.py
)

rem открываем браузер с задержкой, чтобы сервер успел подняться
start "" cmd /c "timeout /t 3 >nul & start "" http://localhost:8000/"

python main.py

rem контейнер Neo4j продолжает работать, остановка - stop_windows.cmd
pause
