import asyncio
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import openai
import pytest

import app.llm_client as llm_client_module
from app.config import Settings
from app.llm_client import LlmClient, is_retryable


def make_error(status: int | None) -> openai.OpenAIError:
    """Собирает ошибку openai с нужным статусом ответа"""
    exc = openai.OpenAIError("тестовая ошибка")
    if status is not None:
        exc.status_code = status
    return exc


def test_is_retryable_network_error() -> None:
    """Ошибку без статуса (сеть, таймаут) имеет смысл повторить"""
    assert is_retryable(make_error(None))


def test_is_retryable_rate_limit_and_server_errors() -> None:
    """429 и 5xx повторяем - лимит и сервер могут отпустить"""
    assert is_retryable(make_error(429))
    assert is_retryable(make_error(500))
    assert is_retryable(make_error(503))


def test_is_retryable_client_errors() -> None:
    """Клиентские 4xx повторять бесполезно"""
    assert not is_retryable(make_error(400))
    assert not is_retryable(make_error(401))
    assert not is_retryable(make_error(404))


class FakeStream:
    """Асинхронный итератор по заранее заготовленным чанкам"""

    def __init__(self, chunks: list[Any]) -> None:
        self._chunks = list(chunks)

    def __aiter__(self) -> "FakeStream":
        return self

    async def __anext__(self) -> Any:
        if not self._chunks:
            raise StopAsyncIteration
        return self._chunks.pop(0)


class FakeCompletions:
    """Подмена chat.completions, отдает ответы по очереди"""

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self._responses.pop(0)


def content_chunk(text: str) -> Any:
    """Чанк стрима с куском текста"""
    delta = SimpleNamespace(content=text, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])


def tool_call_chunk(call_id: str, name: str, arguments: str) -> Any:
    """Чанк стрима с запросом инструмента"""
    call = SimpleNamespace(
        index=0,
        id=call_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )
    delta = SimpleNamespace(content=None, tool_calls=[call])
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])


def make_client(
    settings: Settings, responses: list[Any]
) -> tuple[LlmClient, FakeCompletions]:
    """Клиент с фейковым транспортом вместо OpenRouter"""
    client = LlmClient(settings)
    completions = FakeCompletions(responses)
    client._client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions)
    )
    return client, completions


def collect_tokens(gen: AsyncIterator[str]) -> list[str]:
    """Дочитывает стрим до конца и возвращает токены списком"""

    async def run() -> list[str]:
        return [token async for token in gen]

    return asyncio.run(run())


def test_stream_chat_plain_text(settings: Settings) -> None:
    """Текстовый ответ без инструментов проходит одним кругом"""
    stream = FakeStream([content_chunk("привет"), content_chunk(" мир")])
    client, completions = make_client(settings, [stream])
    messages = [{"role": "user", "content": "поздоровайся"}]
    tokens = collect_tokens(client.stream_chat(messages))
    assert tokens == ["привет", " мир"]
    assert len(completions.calls) == 1
    # на первом круге модель получает список инструментов
    assert completions.calls[0]["tools"]
    # входной список сообщений не изменился
    assert messages == [{"role": "user", "content": "поздоровайся"}]


def test_stream_chat_filters_reasoning(settings: Settings) -> None:
    """Служебные рассуждения модели не доходят до клиента"""
    stream = FakeStream(
        [
            content_chunk("<thought>secret english plan</thought>"),
            content_chunk("Привет"),
        ]
    )
    client, _ = make_client(settings, [stream])
    tokens = collect_tokens(
        client.stream_chat([{"role": "user", "content": "привет"}])
    )
    assert "".join(tokens) == "Привет"


def test_stream_chat_flushes_tail_before_tool(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Хвост фильтра не теряется при переходе к вызову инструмента

    Обычный текст из буфера доезжает до клиента до паузы на
    инструмент, а придержанная строка-маркер выбрасывается
    """

    async def kb_echo(question: str) -> str:
        return "данные: " + question

    monkeypatch.setitem(
        llm_client_module.TOOL_HANDLERS, "kb_echo", kb_echo
    )
    first = FakeStream(
        [
            content_chunk("Смотрю в базе\nThought"),
            tool_call_chunk("call-1", "kb_echo", '{"question": "кто"}'),
        ]
    )
    second = FakeStream([content_chunk("готово")])
    client, _ = make_client(settings, [first, second])
    tokens = collect_tokens(
        client.stream_chat([{"role": "user", "content": "вопрос"}])
    )
    assert "".join(tokens) == "Смотрю в базе\nготово"


def test_stream_chat_runs_tool_and_continues(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Запрос инструмента выполняется, результат уходит в контекст"""

    async def kb_echo(question: str) -> str:
        return "данные: " + question

    monkeypatch.setitem(
        llm_client_module.TOOL_HANDLERS, "kb_echo", kb_echo
    )
    first = FakeStream(
        [tool_call_chunk("call-1", "kb_echo", '{"question": "кто"}')]
    )
    second = FakeStream([content_chunk("готово")])
    client, completions = make_client(settings, [first, second])
    tokens = collect_tokens(
        client.stream_chat([{"role": "user", "content": "вопрос"}])
    )
    assert tokens == ["готово"]
    assert len(completions.calls) == 2
    followup = completions.calls[1]["messages"]
    assert followup[-1]["role"] == "tool"
    assert followup[-1]["tool_call_id"] == "call-1"
    assert followup[-1]["content"] == "данные: кто"
    assert followup[-2]["tool_calls"][0]["function"]["name"] == "kb_echo"


def test_complete_returns_text(settings: Settings) -> None:
    """Непотоковый вызов отдает текст первого choice"""
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(message=SimpleNamespace(content="резюме"))
        ]
    )
    client, _ = make_client(settings, [response])
    result = asyncio.run(client.complete([{"role": "user", "content": "x"}]))
    assert result == "резюме"


def test_complete_empty_choices(settings: Settings) -> None:
    """Ответ без choices не роняет клиента, а дает пустую строку"""
    client, _ = make_client(settings, [SimpleNamespace(choices=[])])
    result = asyncio.run(client.complete([{"role": "user", "content": "x"}]))
    assert result == ""


def run_tool(client: LlmClient, name: str, raw: str) -> str:
    """Короткий помощник для синхронного вызова _run_tool"""
    return asyncio.run(client._run_tool(name, raw))


def test_run_tool_unknown_name(settings: Settings) -> None:
    """Неизвестный инструмент превращается в текст для модели"""
    client, _ = make_client(settings, [])
    assert "не найден" in run_tool(client, "нет_такого", "{}")


def test_run_tool_bad_json(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Кривой json аргументов не роняет ход"""

    async def kb_echo(question: str) -> str:
        return question

    monkeypatch.setitem(
        llm_client_module.TOOL_HANDLERS, "kb_echo", kb_echo
    )
    client, _ = make_client(settings, [])
    assert "разобрать" in run_tool(client, "kb_echo", "{кривой")
    assert "объектом" in run_tool(client, "kb_echo", "[1, 2]")


def test_run_tool_wrong_arguments(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Несовпадение с сигнатурой ловится до вызова обработчика"""

    async def kb_echo(question: str) -> str:
        return question

    monkeypatch.setitem(
        llm_client_module.TOOL_HANDLERS, "kb_echo", kb_echo
    )
    client, _ = make_client(settings, [])
    answer = run_tool(client, "kb_echo", '{"nope": 1}')
    assert "Неверный набор аргументов" in answer


def test_run_tool_internal_type_error_is_reported(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TypeError внутри обработчика не маскируется под плохие аргументы"""

    async def broken_tool(question: str) -> str:
        raise TypeError("сломалось внутри")

    monkeypatch.setitem(
        llm_client_module.TOOL_HANDLERS, "kb_broken", broken_tool
    )
    client, _ = make_client(settings, [])
    answer = run_tool(client, "kb_broken", '{"question": "кто"}')
    assert "Ошибка при вызове инструмента" in answer
    assert "сломалось внутри" in answer
