import asyncio
import inspect
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import openai

from app.config import Settings
from app.reasoning_filter import ReasoningFilter, clean_reasoning
from app.tools import TOOL_HANDLERS, TOOLS_SPEC

logger = logging.getLogger(__name__)


class LlmUnavailableError(RuntimeError):
    """LLM не ответила после всех повторов"""


def is_retryable(exc: openai.OpenAIError) -> bool:
    """Решает, есть ли смысл повторять запрос после такой ошибки

    Клиентские ошибки 4xx повторять бесполезно - неверная модель или
    ключ сами не исправятся. Исключение - 429, лимит может отпустить
    """
    status = getattr(exc, "status_code", None)
    if status is None:
        # сетевая ошибка или таймаут, повтор может помочь
        return True
    return status == 429 or status >= 500


class LlmClient:
    """Потоковый клиент LLM через OpenRouter (OpenAI-совместимый API)"""

    def __init__(self, settings: Settings) -> None:
        """Создает async клиента с ключом и адресом из настроек"""
        self._settings = settings
        # заглушка вместо пустого ключа, иначе клиент падает на старте
        # сервиса, а нам нужен рабочий текстовый режим даже без .env
        self._client = openai.AsyncOpenAI(
            api_key=settings.openrouter_api_key or "missing-key",
            base_url=settings.openrouter_base_url,
        )

    async def stream_chat(
        self, messages: list[dict[str, str]]
    ) -> AsyncIterator[str]:
        """Стримит текстовые куски ответа LLM по мере генерации

        Внутри крутится цикл function calling - если модель вместо
        текста запросила инструмент (например погоду), выполняем его,
        подкладываем результат в контекст и спрашиваем модель снова.
        Наружу уходят только текстовые токены, вызовы инструментов
        для Dialogue Manager прозрачны

        Служебные рассуждения модели (блоки thought и подобные)
        наружу не отдаются - в контекст диалога с моделью идет сырой
        текст, а клиенту только очищенный

        Повторы делаются только до старта стрима, обрыв уже начатой
        генерации отдаем наружу как есть
        """
        # свою копию, входной список сообщений не трогаем
        work_messages: list[dict[str, Any]] = list(messages)
        for round_index in range(self._settings.llm_tool_rounds + 1):
            # на последнем круге инструменты не даем, чтобы модель
            # гарантированно ответила текстом и не зациклилась
            allow_tools = round_index < self._settings.llm_tool_rounds
            stream = await self._create_with_retries(
                work_messages,
                stream=True,
                tools=TOOLS_SPEC if allow_tools else None,
            )
            content_parts: list[str] = []
            tool_calls: dict[int, dict[str, str]] = {}
            reasoning_filter = ReasoningFilter()
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta is None:
                    continue
                if delta.content:
                    content_parts.append(delta.content)
                    cleaned = reasoning_filter.feed(delta.content)
                    if cleaned:
                        yield cleaned
                # аргументы вызова приходят кусками, склеиваем по index
                for call in delta.tool_calls or []:
                    acc = tool_calls.setdefault(
                        call.index, {"id": "", "name": "", "arguments": ""}
                    )
                    if call.id:
                        acc["id"] = call.id
                    if call.function and call.function.name:
                        acc["name"] = call.function.name
                    if call.function and call.function.arguments:
                        acc["arguments"] += call.function.arguments
            tail = reasoning_filter.flush()
            if tail:
                yield tail
            if not tool_calls:
                return
            ordered = [tool_calls[index] for index in sorted(tool_calls)]
            work_messages.append(
                {
                    "role": "assistant",
                    "content": "".join(content_parts) or None,
                    "tool_calls": [
                        {
                            "id": call["id"],
                            "type": "function",
                            "function": {
                                "name": call["name"],
                                "arguments": call["arguments"],
                            },
                        }
                        for call in ordered
                    ],
                }
            )
            for call in ordered:
                logger.info(
                    "LLM запросила инструмент %s с аргументами %s",
                    call["name"],
                    call["arguments"],
                )
                result = await self._run_tool(call["name"], call["arguments"])
                work_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": result,
                    }
                )

    async def _run_tool(self, name: str, raw_arguments: str) -> str:
        """Выполняет инструмент по имени, ошибки превращает в текст

        Любой сбой уходит модели строкой, а не исключением - она
        сможет объяснить пользователю что пошло не так
        """
        handler = TOOL_HANDLERS.get(name)
        if handler is None:
            return f"Инструмент '{name}' не найден"
        try:
            arguments = json.loads(raw_arguments) if raw_arguments else {}
        except ValueError:
            return "Не удалось разобрать аргументы вызова инструмента"
        if not isinstance(arguments, dict):
            return "Аргументы инструмента должны быть объектом"
        # сверяем аргументы с сигнатурой до вызова, иначе TypeError
        # изнутри обработчика маскировался бы под неверные аргументы
        try:
            inspect.signature(handler).bind(**arguments)
        except TypeError:
            return f"Неверный набор аргументов для инструмента '{name}'"
        try:
            return await handler(**arguments)
        except Exception as exc:
            logger.exception("сбой инструмента %s", name)
            return f"Ошибка при вызове инструмента '{name}': {exc}"

    async def complete(self, messages: list[dict[str, str]]) -> str:
        """Непотоковый вызов, используется для сжатия истории в summary"""
        response = await self._create_with_retries(messages, stream=False)
        # OpenRouter при сбое апстрима может вернуть 200 без choices
        if not response.choices:
            return ""
        content = response.choices[0].message.content
        # рассуждения модели в summary тоже не нужны
        return clean_reasoning(content or "")

    async def _create_with_retries(
        self,
        messages: list[dict[str, Any]],
        stream: bool,
        tools: list[dict[str, Any]] | None = None,
    ) -> Any:
        """Вызывает chat.completions с повторами и растущей паузой"""
        extra: dict[str, Any] = {}
        if tools:
            extra["tools"] = tools
        last_error: Exception | None = None
        for attempt in range(self._settings.llm_retries):
            try:
                return await self._client.chat.completions.create(
                    model=self._settings.openrouter_model,
                    messages=messages,
                    stream=stream,
                    **extra,
                )
            except openai.OpenAIError as exc:
                last_error = exc
                if not is_retryable(exc):
                    logger.error("LLM отклонила запрос: %s", exc)
                    break
                if attempt >= self._settings.llm_retries - 1:
                    # попытки кончились, ждать перед ошибкой смысла нет
                    break
                delay = self._settings.llm_retry_base_delay * 2 ** attempt
                logger.warning(
                    "ошибка LLM, попытка %d из %d, повтор через %.1f c: %s",
                    attempt + 1,
                    self._settings.llm_retries,
                    delay,
                    exc,
                )
                await asyncio.sleep(delay)
        raise LlmUnavailableError(
            "LLM недоступна, попробуйте еще раз позже"
        ) from last_error
