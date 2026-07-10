import asyncio
import json
import uuid

from fastapi import WebSocket, WebSocketDisconnect

from app.config import Settings
from app.dialogue_manager import DialogueManager
from app.llm_client import LlmClient
from app.memory import DialogueMemory
from app.stt import SttEngine, SttUnavailableError
from app.tts import TtsEngine

# записи короче четверти секунды считаем случайным нажатием
MIN_UTTERANCE_BYTES = 8000


class WsSession:
    """Состояние одного websocket соединения

    Gateway остается тонким транспортным слоем - он буферизует аудио и
    маршрутизирует события, вся логика диалога в Dialogue Manager
    """

    def __init__(
        self,
        ws: WebSocket,
        settings: Settings,
        stt: SttEngine,
        tts: TtsEngine,
        llm: LlmClient,
    ) -> None:
        """Создает память диалога и Dialogue Manager для этой сессии"""
        self.session_id = uuid.uuid4().hex
        self._ws = ws
        self._stt = stt
        self.audio_buffer = bytearray()
        self.recording = False
        self._stt_task: asyncio.Task | None = None
        memory = DialogueMemory(
            system_prompt=settings.system_prompt,
            keep_last_turns=settings.keep_last_turns,
            summarize_trigger_tokens=settings.summarize_trigger_tokens,
        )
        self.manager = DialogueManager(
            memory=memory,
            llm=llm,
            tts=tts,
            send_json=self.send_json,
            send_bytes=self.send_bytes,
        )

    async def send_json(self, payload: dict) -> None:
        """Шлет клиенту текстовый JSON кадр"""
        try:
            await self._ws.send_text(json.dumps(payload, ensure_ascii=False))
        except (RuntimeError, WebSocketDisconnect):
            # соединение уже закрыто, событие можно молча выбросить
            pass

    async def send_bytes(self, payload: bytes) -> None:
        """Шлет клиенту бинарный кадр с аудио"""
        try:
            await self._ws.send_bytes(payload)
        except (RuntimeError, WebSocketDisconnect):
            pass

    async def on_start(self) -> None:
        """Начало записи - чистим буфер и начинаем копить аудио"""
        self.audio_buffer = bytearray()
        self.recording = True

    async def on_stop(self) -> None:
        """Конец записи - распознаем реплику целиком и запускаем ход"""
        self.recording = False
        pcm = bytes(self.audio_buffer)
        self.audio_buffer = bytearray()
        if len(pcm) < MIN_UTTERANCE_BYTES:
            await self.send_json(
                {"type": "error", "message": "запись слишком короткая"}
            )
            return
        # распознаем в отдельной задаче, чтобы не блокировать прием кадров
        # прошлую незавершенную задачу отменяем, иначе при быстрых
        # повторных записях реплики попадут в диалог не по порядку
        task = self._stt_task
        if task is not None and not task.done():
            task.cancel()
        self._stt_task = asyncio.create_task(self._process_utterance(pcm))

    async def on_barge_in(self) -> None:
        """Перебивание - отменяем текущий ход, новый начнет клиент"""
        await self.manager.cancel_turn()

    async def on_text_input(self, text: str) -> None:
        """Текстовый fallback, работает и без микрофона"""
        clean = text.strip()
        if not clean:
            return
        await self.send_json({"type": "final_transcript", "text": clean})
        await self.manager.handle_user_text(clean)

    async def shutdown(self) -> None:
        """Чистит буфер и незавершенный ход при разрыве соединения"""
        self.audio_buffer = bytearray()
        task = self._stt_task
        if task is not None and not task.done():
            task.cancel()
        await self.manager.cancel_turn()

    async def _process_utterance(self, pcm: bytes) -> None:
        """Распознает записаную реплику и передает ее Dialogue Manager"""
        if not self._stt.available:
            await self.send_json(
                {
                    "type": "error",
                    "message": "локальный STT не установлен, "
                    "используйте текстовый ввод",
                }
            )
            return
        try:
            text = await self._stt.transcribe(pcm)
        except SttUnavailableError as exc:
            await self.send_json({"type": "error", "message": str(exc)})
            return
        except asyncio.CancelledError:
            raise
        except Exception:
            await self.send_json(
                {"type": "error", "message": "ошибка распознавания речи"}
            )
            return
        if not text:
            await self.send_json(
                {"type": "error", "message": "речь не распознана"}
            )
            return
        await self.send_json({"type": "final_transcript", "text": text})
        await self.manager.handle_user_text(text)


async def handle_client_event(session: WsSession, raw: str) -> None:
    """Разбирает управляющее JSON сообщение от клиента"""
    try:
        event = json.loads(raw)
    except json.JSONDecodeError:
        await session.send_json(
            {"type": "error", "message": "не удалось разобрать сообщение"}
        )
        return
    event_type = event.get("type")
    if event_type == "start":
        await session.on_start()
    elif event_type == "stop":
        await session.on_stop()
    elif event_type == "barge_in":
        await session.on_barge_in()
    elif event_type == "text_input":
        await session.on_text_input(str(event.get("text", "")))
    else:
        await session.send_json(
            {"type": "error", "message": f"неизвестный тип: {event_type}"}
        )


async def handle_websocket(
    ws: WebSocket,
    settings: Settings,
    stt: SttEngine,
    tts: TtsEngine,
    llm: LlmClient,
) -> None:
    """Обслуживает одно соединение - принимает аудио и команды клиента"""
    await ws.accept()
    session = WsSession(ws, settings, stt, tts, llm)
    await session.send_json(
        {"type": "ready", "session_id": session.session_id}
    )
    try:
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break
            data = message.get("bytes")
            if data is not None:
                # бинарные кадры вверх - только аудио с микрофона
                if session.recording:
                    session.audio_buffer.extend(data)
                continue
            text = message.get("text")
            if text:
                await handle_client_event(session, text)
    except WebSocketDisconnect:
        pass
    finally:
        await session.shutdown()
