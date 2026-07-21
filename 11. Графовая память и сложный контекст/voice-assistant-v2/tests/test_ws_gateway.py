import asyncio
import json

import pytest

import app.ws_gateway as ws_gateway_module
from app.config import Settings
from app.llm_client import LlmClient
from app.tts import TtsEngine
from app.ws_gateway import (
    MIN_UTTERANCE_BYTES,
    WsSession,
    handle_client_event,
    notify_when_kb_ready,
)


class FakeWs:
    """Websocket-заглушка, складывает отправленные события в список"""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))

    async def send_bytes(self, data: bytes) -> None:
        pass


class FakeStt:
    """Замена STT движка с заранее известным результатом"""

    def __init__(self, text: str = "привет", available: bool = True) -> None:
        self.available = available
        self._text = text

    async def transcribe(self, pcm: bytes) -> str:
        return self._text


class ManagerRecorder:
    """Записывает обращения гейтвея к Dialogue Manager"""

    def __init__(self) -> None:
        self.texts: list[str] = []
        self.cancelled = 0

    async def handle_user_text(self, text: str) -> None:
        self.texts.append(text)

    async def cancel_turn(self) -> None:
        self.cancelled += 1


def make_session(
    settings: Settings, stt: FakeStt | None = None
) -> tuple[WsSession, FakeWs, ManagerRecorder]:
    """Сессия на фейках - настоящий менеджер подменяется рекордером"""
    ws = FakeWs()
    session = WsSession(
        ws,
        settings,
        stt or FakeStt(),
        TtsEngine(settings),
        LlmClient(settings),
    )
    recorder = ManagerRecorder()
    session.manager = recorder
    return session, ws, recorder


def event_types(ws: FakeWs) -> list[str]:
    """Типы событий, отправленых клиенту"""
    return [event["type"] for event in ws.sent]


def test_start_resets_buffer(settings: Settings) -> None:
    """Начало записи чистит буфер и включает прием аудио"""
    session, _, _ = make_session(settings)
    session.audio_buffer.extend(b"\x00\x01")

    asyncio.run(session.on_start())
    assert session.recording
    assert session.audio_buffer == bytearray()


def test_short_recording_rejected(settings: Settings) -> None:
    """Слишком короткая запись не идет в распознавание"""
    session, ws, recorder = make_session(settings)

    async def scenario() -> None:
        await session.on_start()
        session.audio_buffer.extend(b"\x00" * 10)
        await session.on_stop()

    asyncio.run(scenario())
    assert event_types(ws) == ["error"]
    assert "короткая" in ws.sent[0]["message"]
    assert recorder.texts == []


def test_stop_runs_stt_and_starts_turn(settings: Settings) -> None:
    """Нормальная запись распознается и запускает ход диалога"""
    session, ws, recorder = make_session(settings, FakeStt(text="привет"))

    async def scenario() -> None:
        await session.on_start()
        session.audio_buffer.extend(b"\x00" * MIN_UTTERANCE_BYTES)
        await session.on_stop()
        await session._stt_task

    asyncio.run(scenario())
    assert {"type": "final_transcript", "text": "привет"} in ws.sent
    assert recorder.texts == ["привет"]


def test_unrecognized_speech(settings: Settings) -> None:
    """Пустой транскрипт отдается клиенту ошибкой, ход не стартует"""
    session, ws, recorder = make_session(settings, FakeStt(text=""))

    async def scenario() -> None:
        await session.on_start()
        session.audio_buffer.extend(b"\x00" * MIN_UTTERANCE_BYTES)
        await session.on_stop()
        await session._stt_task

    asyncio.run(scenario())
    assert "error" in event_types(ws)
    assert recorder.texts == []


def test_stt_not_installed(settings: Settings) -> None:
    """Без локального STT клиент получает подсказку про текстовый ввод"""
    session, ws, recorder = make_session(
        settings, FakeStt(available=False)
    )

    async def scenario() -> None:
        await session.on_start()
        session.audio_buffer.extend(b"\x00" * MIN_UTTERANCE_BYTES)
        await session.on_stop()
        await session._stt_task

    asyncio.run(scenario())
    assert "error" in event_types(ws)
    assert recorder.texts == []


def test_text_input_starts_turn(settings: Settings) -> None:
    """Текстовый fallback шлет транскрипт и запускает ход"""
    session, ws, recorder = make_session(settings)
    raw = json.dumps({"type": "text_input", "text": "  привет  "})
    asyncio.run(handle_client_event(session, raw))
    assert {"type": "final_transcript", "text": "привет"} in ws.sent
    assert recorder.texts == ["привет"]


def test_text_input_blank_is_ignored(settings: Settings) -> None:
    """Пустой текст не порождает ни событий, ни хода"""
    session, ws, recorder = make_session(settings)
    raw = json.dumps({"type": "text_input", "text": "   "})
    asyncio.run(handle_client_event(session, raw))
    assert ws.sent == []
    assert recorder.texts == []


def test_barge_in_cancels_turn(settings: Settings) -> None:
    """Перебивание транслируется в отмену текущего хода"""
    session, _, recorder = make_session(settings)
    raw = json.dumps({"type": "barge_in"})
    asyncio.run(handle_client_event(session, raw))
    assert recorder.cancelled == 1


def test_bad_json_reported(settings: Settings) -> None:
    """Нечитаемое сообщение отдается клиенту ошибкой"""
    session, ws, _ = make_session(settings)
    asyncio.run(handle_client_event(session, "{кривой json"))
    assert event_types(ws) == ["error"]


def test_unknown_event_type_reported(settings: Settings) -> None:
    """Неизвестный тип события не игнорируется молча"""
    session, ws, _ = make_session(settings)
    raw = json.dumps({"type": "странное"})
    asyncio.run(handle_client_event(session, raw))
    assert event_types(ws) == ["error"]
    assert "неизвестный тип" in ws.sent[0]["message"]


def test_notify_when_kb_ready(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """После окончания прогрева клиенту уходит kb_ready"""
    session, ws, _ = make_session(settings)

    async def fake_wait() -> None:
        return

    monkeypatch.setattr(ws_gateway_module, "wait_warmup", fake_wait)
    asyncio.run(notify_when_kb_ready(session))
    assert {"type": "kb_ready"} in ws.sent
