import os
import re

from pydantic_settings import BaseSettings, SettingsConfigDict

# переменные окружения с адресом прокси, которые читает httpx
PROXY_VARIABLES = ("all_proxy", "http_proxy", "https_proxy")


def normalized_proxy_url(url: str) -> str:
    """Приводит схему socks:// к socks5://, понятной httpx"""
    return re.sub(r"^socks://", "socks5://", url, flags=re.IGNORECASE)


def normalize_proxy_env() -> None:
    """Чинит переменные прокси в окружении процесса

    Рабочие столы Linux (GNOME и другие) выставляют all_proxy со
    схемой socks://, а httpx такой схемы не знает и падает еще на
    создании клиента - ломаются и openai, и huggingface_hub. На
    практике socks:// означает socks5, поэтому просто переписываем
    схему. Зовется один раз на старте точек входа
    """
    for name in PROXY_VARIABLES:
        for variable in (name, name.upper()):
            value = os.environ.get(variable)
            if value:
                os.environ[variable] = normalized_proxy_url(value)


class Settings(BaseSettings):
    """Конфигурация сервиса, значения читаются из .env"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = "0.0.0.0"
    port: int = 8000

    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_model: str = "google/gemini-3.5-flash"

    whisper_model: str = "small"
    whisper_device: str = "cpu"
    whisper_language: str = "ru"

    tts_engine: str = "piper"
    tts_voice: str = "models/ru_RU-irina-medium.onnx"

    system_prompt: str = "Ты полезный голосовой ассистент. Отвечай кратко."
    keep_last_turns: int = 6
    summarize_trigger_tokens: int = 3000

    # повторы при отказе внешнего API
    llm_retries: int = 3
    llm_retry_base_delay: float = 1.0

    # сколько раундов вызова инструментов разрешаем за один ход,
    # защита от зацикливания модели на функциях
    llm_tool_rounds: int = 3

    # Neo4j, значения по умолчанию совпадают с docker-compose.yml
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_username: str = "neo4j"
    neo4j_password: str = "graphdemo42"
    neo4j_database: str = "neo4j"

    # гибридный поиск по базе знаний
    embedding_model: str = "intfloat/multilingual-e5-small"
    # сколько фрагментов документов берем из векторного поиска
    kb_top_k: int = 4
    # глубина обхода графа от упомянутых сущностей
    kb_max_hops: int = 2
    # предел фактов в контексте инструмента
    kb_max_facts: int = 30
