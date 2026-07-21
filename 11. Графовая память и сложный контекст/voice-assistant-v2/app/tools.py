from typing import Any

from app.knowledge import (
    AGGREGATE_TOOL_SPEC,
    KNOWLEDGE_TOOL_SPEC,
    aggregate_knowledge,
    search_knowledge,
)
from app.weather import WEATHER_TOOL_SPEC, get_weather

# общий реестр инструментов для LLM клиента, при добавлении нового
# инструмента достаточно дописать его сюда
TOOLS_SPEC: list[dict[str, Any]] = [
    WEATHER_TOOL_SPEC,
    KNOWLEDGE_TOOL_SPEC,
    AGGREGATE_TOOL_SPEC,
]

TOOL_HANDLERS: dict[str, Any] = {
    "get_weather": get_weather,
    "search_knowledge": search_knowledge,
    "aggregate_knowledge": aggregate_knowledge,
}
