# voice-assistant

Учебный демо-сервис голосового ассистента (ДЗ к уроку 9).
Монолит на FastAPI: браузер шлет голос по WebSocket, сервер распознает
его локально (faster-whisper), отправляет текст в LLM через OpenRouter
и стримит обратно токены ответа и озвучку (Piper). Контекст диалога
живет в RAM, при разрастании сжимается в rolling summary.

Подробности архитектуры - в ../ARCHITECTURE.md.

## Соответствие критериям ДЗ

- передача контекста - источником знаний выступает сама LLM, контекст
  диалога (system prompt, summary, последние реплики) собирается в
  `app/memory.py` и передается в модель на каждом ходе
- streaming ответа - токены LLM и аудио TTS стримятся в браузер по
  одному WebSocket по мере генерации
- воспроизводимость - кроме голоса есть текстовый ввод, он работает
  без микрофона и без аудио-зависимостей
- function calling - у модели есть инструмент get_weather
  (`app/weather.py`), на вопрос про погоду в городе она сама зовет
  Open-Meteo API (геокодинг названия плюс текущая погода) и отвечает
  по полученным данным, цикл вызова инструментов живет в
  `app/llm_client.py`

## Установка

Нужен Python 3.13

Быстрый способ - готовые скрипты, они создают .venv рядом с собой,
ставят зависимости, копируют .env.example в .env и при полном
варианте скачивают голос для Piper:

```bash
# Linux / macOS
./install_linux.sh          # полный вариант с STT и TTS
./install_linux.sh min      # минимальный вариант, только текст
```

```powershell
# Windows 11
install_windows.cmd         # полный вариант с STT и TTS
install_windows.cmd min     # минимальный вариант, только текст
```

После установки впишите свой OPENROUTER_API_KEY в .env.

Дальше в этом разделе те же шаги описаны для ручной установки.

Создание и активация виртуального окружения.

### Linux / macOS:

```bash
cd voice-assistant
python3 -m venv .venv
source .venv/bin/activate
```

### Windows 11 (PowerShell или cmd):

Важно:
в пути проекта не должно быть кириллических символов

```powershell
cd voice-assistant
py -3.13 -m venv .venv
.venv\Scripts\activate
```

Если PowerShell ругается на политику выполнения скриптов, разрешите
локальные скрипты для текущего пользователя:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

Установка зависимостей (одинаково для всех ОС):

```bash
# минимальный вариант - только текстовый режим
pip install -e .

# полный вариант - с локальным STT и TTS
pip install -e ".[audio]"

# группа dev добавляет pytest для unit тестов, скрипты установки
# ставят ее сами, при ручной установке комбинируйте с нужным вариантом
pip install -e ".[dev]"
pip install -e ".[audio,dev]"
```

Конфигурация:

```bash
# Linux / macOS / PowerShell
cp .env.example .env
# в cmd вместо cp используйте: copy .env.example .env
# впишите свой OPENROUTER_API_KEY
```

Голос для Piper (только для полного варианта):

```bash
mkdir models
python -m piper.download_voices ru_RU-irina-medium --data-dir models
# путь к .onnx файлу укажите в TTS_VOICE в .env
```

Модель Whisper скачается сама при первом распознавании, поэтому
первый голосовой ход может занять заметное время.

## Запуск

### Linux / macOS:

```bash
./run_linux.sh
```

Скрипт активирует .venv, запускает сервер и сам открывает
http://localhost:8000 в браузере. Ручной вариант - активировать
окружение и выполнить `python main.py`.

Замечание про микрофон: getUserMedia работает только в secure
context, то есть на https или на localhost. Если открываете сервис
по адресу вида http://192.168.x.x, голос будет недоступен и
останется текстовый режим.

### Windows 11 (PowerShell или cmd):

Запустите run_windows.cmd

## Сценарий для демонстрации

1. Откройте страницу, дождитесь статуса "готов"
2. Зажмите кнопку, скажите "Привет, меня зовут Эрик", отпустите
3. Дождитесь озвученного ответа
4. Спросите голосом или текстом "Как меня зовут?" - ассистент помнит
   контекст и ответит правильно
5. Для перебивания зажмите кнопку прямо во время ответа - генерация
   и озвучка оборвутся, начнется новый ход

## Протокол WebSocket

Одно соединение `/ws`, кадры двух типов. Бинарные: вверх - PCM16
16 kHz с микрофона, вниз - PCM16 озвучки. Текстовые - JSON с полем
`type`:

- от клиента: `start`, `stop`, `barge_in`, `text_input`
- от сервера: `ready`, `final_transcript`, `llm_token`,
  `tts_start`, `tts_end`, `turn_done`, `error`

`text_input` - текстовый fallback, в остальном ход обрабатывается
так же, как голосовой.

## Структура

```text
main.py                   # FastAPI, /ws, отдача статики
app/
├── config.py             # pydantic-settings, чтение .env
├── ws_gateway.py         # websocket, буфер аудио, маршрутизация
├── dialogue_manager.py   # контекст, оркестрация хода, barge-in
├── memory.py             # история, rolling summary, бюджет токенов
├── llm_client.py         # OpenRouter streaming, ретраи, инструменты
├── weather.py            # инструмент get_weather, Open-Meteo API
├── stt.py                # faster-whisper
├── tts.py                # piper
└── static/               # фронтенд без сборщика
tests/                    # unit тесты чистых функций
```
