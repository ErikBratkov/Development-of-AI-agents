import asyncio
from collections.abc import AsyncIterator

from app.dialogue_manager import (
    DialogueManager,
    format_turns_for_summary,
    split_sentences,
)
from app.llm_client import LlmUnavailableError
from app.memory import DialogueMemory, Turn


class FakeLlm:
    """Управляемая замена LLM клиента для проверки хода диалога"""

    def __init__(
        self,
        tokens: list[str] | None = None,
        error: Exception | None = None,
        hang: bool = False,
        complete_error: Exception | None = None,
    ) -> None:
        self._tokens = tokens or []
        self._error = error
        self._hang = hang
        self._complete_error = complete_error

    async def stream_chat(
        self, messages: list[dict[str, str]]
    ) -> AsyncIterator[str]:
        """Отдает заготовленные токены, потом ошибку или зависание"""
        for token in self._tokens:
            yield token
        if self._hang:
            # висим до отмены, имитируя долгую генерацию
            await asyncio.Event().wait()
        if self._error is not None:
            raise self._error

    async def complete(self, messages: list[dict[str, str]]) -> str:
        """Либо резюме, либо заготовленный сбой сжатия"""
        if self._complete_error is not None:
            raise self._complete_error
        return "резюме разговора"


class FakeTts:
    """Замена TTS - запоминает предложения и отдает байты-заглушки"""

    def __init__(self, available: bool = True) -> None:
        self.available = available
        self.spoken: list[str] = []

    async def get_sample_rate(self) -> int:
        return 22050

    async def synthesize(self, text: str) -> bytes:
        self.spoken.append(text)
        return b"\x01\x02"


class Collector:
    """Копилка событий и аудио, уходящих клиенту"""

    def __init__(self) -> None:
        self.events: list[dict] = []
        self.audio: list[bytes] = []

    async def send_json(self, payload: dict) -> None:
        self.events.append(payload)

    async def send_bytes(self, payload: bytes) -> None:
        self.audio.append(payload)

    def types(self) -> list[str]:
        return [event["type"] for event in self.events]


def make_manager(
    memory: DialogueMemory, llm: FakeLlm, tts: FakeTts | None = None
) -> tuple[DialogueManager, Collector, FakeTts]:
    """Собирает Dialogue Manager на фейках"""
    collector = Collector()
    tts = tts if tts is not None else FakeTts()
    manager = DialogueManager(
        memory=memory,
        llm=llm,
        tts=tts,
        send_json=collector.send_json,
        send_bytes=collector.send_bytes,
    )
    return manager, collector, tts


def test_run_turn_happy_path(memory: DialogueMemory) -> None:
    """Успешный ход - ответ в истории, озвучка и turn_done клиенту"""
    llm = FakeLlm(tokens=["Привет! ", "Как дела?"])
    manager, collector, tts = make_manager(memory, llm)
    asyncio.run(manager._run_turn("привет"))
    assert memory.turns[-1] == Turn(
        role="assistant", text="Привет! Как дела?"
    )
    types = collector.types()
    assert "tts_start" in types
    assert "tts_end" in types
    assert types.count("turn_done") == 1
    assert tts.spoken == ["Привет!", "Как дела?"]
    assert collector.audio


def test_run_turn_llm_unavailable(memory: DialogueMemory) -> None:
    """Отказ LLM не портит историю и закрывает ход ошибкой"""
    llm = FakeLlm(error=LlmUnavailableError("LLM недоступна"))
    manager, collector, _ = make_manager(memory, llm)
    asyncio.run(manager._run_turn("привет"))
    assert all(turn.role == "user" for turn in memory.turns)
    types = collector.types()
    assert "error" in types
    assert types.count("turn_done") == 1


def test_run_turn_records_partial_on_stream_failure(
    memory: DialogueMemory,
) -> None:
    """Обрыв стрима фиксирует уже отданный клиенту кусок ответа"""
    llm = FakeLlm(
        tokens=["Начало ответа. ", "Прод"], error=RuntimeError("обрыв")
    )
    manager, collector, _ = make_manager(memory, llm)
    asyncio.run(manager._run_turn("вопрос"))
    assert memory.turns[-1] == Turn(
        role="assistant", text="Начало ответа. Прод"
    )
    types = collector.types()
    assert "error" in types
    assert types.count("turn_done") == 1


def test_summarize_failure_does_not_duplicate_answer(
    memory: DialogueMemory,
) -> None:
    """Сбой сжатия истории не дублирует ответ и не шлет ошибку"""
    for index in range(3):
        memory.add_user("вопрос номер " + str(index) + " " + "д" * 60)
        memory.add_assistant("ответ номер " + str(index) + " " + "о" * 60)
    llm = FakeLlm(
        tokens=["Ответ готов."],
        complete_error=RuntimeError("сбой сжатия"),
    )
    manager, collector, _ = make_manager(memory, llm)
    asyncio.run(manager._run_turn("вопрос"))
    answers = [t for t in memory.turns if t.text == "Ответ готов."]
    assert len(answers) == 1
    types = collector.types()
    assert types.count("turn_done") == 1
    assert "error" not in types


def test_cancel_turn_records_spoken_part(memory: DialogueMemory) -> None:
    """Перебивание пишет в историю только озвученную часть"""
    llm = FakeLlm(tokens=["Раз. Два. "], hang=True)
    manager, collector, _ = make_manager(memory, llm)

    async def scenario() -> None:
        await manager.handle_user_text("вопрос")
        # даем ходу дойти до зависания в генерации
        await asyncio.sleep(0.05)
        await manager.cancel_turn()

    asyncio.run(scenario())
    assert memory.turns[-1] == Turn(role="assistant", text="Раз. Два.")
    types = collector.types()
    assert "tts_end" in types
    assert "turn_done" in types


def test_cancel_turn_without_tts_start(memory: DialogueMemory) -> None:
    """Если озвучка не начиналась, tts_end при отмене не шлется"""
    llm = FakeLlm(hang=True)
    manager, collector, _ = make_manager(
        memory, llm, FakeTts(available=False)
    )

    async def scenario() -> None:
        await manager.handle_user_text("вопрос")
        await asyncio.sleep(0.05)
        await manager.cancel_turn()

    asyncio.run(scenario())
    types = collector.types()
    assert "tts_end" not in types
    assert "turn_done" in types


def test_run_turn_without_tts(memory: DialogueMemory) -> None:
    """Без TTS ответ идет только текстом, аудио-событий нет"""
    llm = FakeLlm(tokens=["Ответ текстом."])
    manager, collector, _ = make_manager(
        memory, llm, FakeTts(available=False)
    )
    asyncio.run(manager._run_turn("вопрос"))
    types = collector.types()
    assert "tts_start" not in types
    assert "tts_end" not in types
    assert collector.audio == []
    assert memory.turns[-1].text == "Ответ текстом."


def test_split_sentences_cuts_finished() -> None:
    """Законченные предложения отрезаются, хвост остается в буфере"""
    sentences, rest = split_sentences("Привет! Как дела? Я тут дума")
    assert sentences == ["Привет!", "Как дела?"]
    assert rest == "Я тут дума"


def test_split_sentences_no_end_mark() -> None:
    """Без знака конца ничего не отрезается"""
    sentences, rest = split_sentences("просто текст без знаков")
    assert sentences == []
    assert rest == "просто текст без знаков"


def test_split_sentences_needs_space_after_mark() -> None:
    """Предложение без пробела после знака еще может дописываться"""
    sentences, rest = split_sentences("Готово.")
    assert sentences == []
    assert rest == "Готово."


def test_split_sentences_multiple_marks() -> None:
    """Несколько знаков подряд считаются одним концом предложения"""
    sentences, rest = split_sentences("Да неужели?! Вот это новости. ")
    assert sentences == ["Да неужели?!", "Вот это новости."]
    assert rest == ""


def test_format_turns_for_summary() -> None:
    """Реплики превращаются в плоский текст с ролями"""
    turns = [
        Turn(role="user", text="привет"),
        Turn(role="assistant", text="здравствуйте"),
    ]
    text = format_turns_for_summary(turns)
    assert text == "Пользователь: привет\nАссистент: здравствуйте"
