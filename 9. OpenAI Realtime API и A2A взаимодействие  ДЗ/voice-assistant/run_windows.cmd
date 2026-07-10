@echo off
rem запуск voice-assistant на Windows 11
rem перед первым запуском выполните install_windows.cmd
chcp 65001 >nul

cd /d "%~dp0"
call .venv\Scripts\activate

rem открываем браузер с задержкой, чтобы сервер успел подняться
start "" cmd /c "timeout /t 3 >nul & start "" http://localhost:8000/"

python main.py
pause
