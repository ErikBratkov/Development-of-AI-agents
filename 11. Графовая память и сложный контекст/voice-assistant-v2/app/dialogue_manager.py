import asyncio
import logging
import re
from collections.abc import Awaitable, Callable

from app.llm_client import LlmClient, LlmUnavailableError
from app.memory import DialogueMemory, Turn
from app.tts import TtsEngine

logger = logging.getLogger(__name__)

SendJson = Callable[[dict], Awaitable[None]]
SendBytes = Callable[[bytes], Awaitable[None]]

# законченное предложение - знак конца плюс пробел, хвост без знака
# дошлем в TTS после конца генерации
_SENTENCE_END = re.compile(r"(.*?[.!?]+)\s+", re.S)

SUMMARY_PROMPT = (
    "Сожми приведенный диалог в краткое резюме на русском языке. "
    "Если в начале дано резюме более ранней части разговора, "
    "объедини его с новыми репликами в одно целое. Сохрани имена, "
    "факты и договоренности, убери воду. Ответь только текстом резюме."
)


def split_sentences(buffer: str) -> tuple[list[str], str]:
    """Отрезает от буфера законченные предложения и возвращает остаток"""
    sentences: list[str] = []
    rest = buffer
    while True:
        match = _SENTENCE_END.match(rest)
        if match is None:
            break
        sentence = match.group(1).strip()
        if sentence:
            sentences.append(sentence)
        rest = rest[match.end():]
    return sentences, rest


def format_turns_for_summary(turns: list[Turn]) -> str:
    """Собирает реплики в плоский текст для промпта сжатия"""
    lines = []
    for turn in turns:
        who = "Пользователь" if turn.role == "user" else "Ассистент"
        lines.append(who + ": " + turn.text)
    return "\n".join(lines)


class DialogueManager:
    """Контур B - единственный владелец истории и контекста диалога

    Принимает финальные реплики пользователя, гоняет потоковый контур
    LLM -> TTS и решает, когда сжимать историю в summary
    """

    def __init__(
        self,
        memory: DialogueMemory,
        llm: LlmClient,
        tts: TtsEngine,
        send_json: SendJson,
        send_bytes: SendBytes,
    ) -> None:
        """Связывает память, клиентов и колбэки отправки в websocket"""
        self._memory = memory
        self._llm = llm
        self._tts = tts
        self._send_json = send_json
        self._send_bytes = send_bytes
        self._turn_task: asyncio.Task | None = None
        # состояние текущего хода
        self._spoken_parts: list[str] = []
        self._tts_started = False
        self._tts_broken = False

    async def handle_user_text(self, text: str) -> None:
        """Стартует новый ход по финальной реплике пользователя"""
        await self.cancel_turn()
        self._turn_task = asyncio.create_task(self._run_turn(text))

    async def cancel_turn(self) -> None:
        """Отменяет текущий ход, например при перебивании

        Частично произнесенный ответ ход сам допишет в историю в своем
        обработчике CancelledError
        """
        task = self._turn_task
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        # tts_end нужен только если озвучка в этом ходе успела начаться
        if self._tts_started:
            await self._send_json({"type": "tts_end"})
        await self._send_json({"type": "turn_done"})

    async def _run_turn(self, user_text: str) -> None:
        """Один ход - контекст, стрим LLM, озвучка, запись в историю"""
        self._memory.add_user(user_text)
        messages = self._memory.build_messages()
        self._spoken_parts = []
        self._tts_started = False
        self._tts_broken = False
        answer_parts: list[str] = []
        buffer = ""
        try:
            async for token in self._llm.stream_chat(messages):
                answer_parts.append(token)
                await self._send_json({"type": "llm_token", "text": token})
                buffer += token
                sentences, buffer = split_sentences(buffer)
                for sentence in sentences:
                    await self._speak(sentence)
            tail = buffer.strip()
            if tail:
                await self._speak(tail)
            answer = "".join(answer_parts).strip()
            if answer:
                self._memory.add_assistant(answer)
            # ответ записан целиком, при поздней отмене или ошибке
            # (например в сжатии истории) дублировать нечего
            self._spoken_parts = []
            answer_parts = []
            if self._tts_started:
                await self._send_json({"type": "tts_end"})
            await self._send_json({"type": "turn_done"})
            await self._maybe_summarize()
        except LlmUnavailableError as exc:
            # историю не портим - реплика пользователя остается,
            # ответ ассистента не добавляется
            await self._send_json({"type": "error", "message": str(exc)})
            await self._send_json({"type": "turn_done"})
        except asyncio.CancelledError:
            # перебивание - в историю идет то, что успели отдать в TTS
            spoken = " ".join(self._spoken_parts).strip()
            if spoken:
                self._memory.add_assistant(spoken)
            raise
        except Exception:
            # стрим оборвался уже после старта генерации, повторов на
            # этом этапе нет. Фиксируем в истории то, что успели отдать
            # клиенту, и закрываем ход, иначе клиент зависнет в ожидании
            logger.exception("ход прервался неожиданной ошибкой")
            partial = "".join(answer_parts).strip()
            if partial:
                self._memory.add_assistant(partial)
            if self._tts_started:
                await self._send_json({"type": "tts_end"})
            await self._send_json(
                {
                    "type": "error",
                    "message": "ответ прервался, попробуйте еще раз",
                }
            )
            await self._send_json({"type": "turn_done"})

    async def _speak(self, sentence: str) -> None:
        """Отправляет предложение в TTS и стримит аудио клиенту

        Отказ TTS не роняет ход, ответ продолжает идти текстом
        """
        if self._tts_broken or not self._tts.available:
            return
        try:
            if not self._tts_started:
                rate = await self._tts.get_sample_rate()
                await self._send_json(
                    {"type": "tts_start", "sample_rate": rate}
                )
                self._tts_started = True
            audio = await self._tts.synthesize(sentence)
        except asyncio.CancelledError:
            raise
        except Exception:
            # piper может упасть по-разному, наружу это не выпускаем
            self._tts_broken = True
            await self._send_json(
                {
                    "type": "error",
                    "message": "озвучка недоступна, ответ придет текстом",
                }
            )
            return
        self._spoken_parts.append(sentence)
        if audio:
            await self._send_bytes(audio)

    async def _maybe_summarize(self) -> None:
        """Сжимает старую часть истории когда она выросла сверх бюджета"""
        if not self._memory.needs_summary():
            return
        old, recent = self._memory.split_for_summary()
        if not old:
            return
        # старое резюме подкладываем в тот же запрос, модель объединит
        # его с новыми репликами - так summary не растет бесконечно
        source_text = format_turns_for_summary(old)
        if self._memory.summary:
            source_text = (
                "Резюме более ранней части разговора:\n"
                + self._memory.summary
                + "\n\nПродолжение диалога:\n"
                + source_text
            )
        prompt_messages = [
            {"role": "system", "content": SUMMARY_PROMPT},
            {"role": "user", "content": source_text},
        ]
        try:
            summary = await self._llm.complete(prompt_messages)
        except LlmUnavailableError:
            # не получилось сжать - попробуем после следующего хода
            return
        except Exception:
            # сжатие не должно ломать уже завершенный ход, поэтому
            # любую неожиданную ошибку глотаем и ждем следующего раза
            logger.exception("сбой сжатия истории в summary")
            return
        if summary.strip():
            self._memory.apply_summary(summary.strip(), recent)
