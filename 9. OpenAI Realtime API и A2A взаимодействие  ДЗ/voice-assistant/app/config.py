from pydantic_settings import BaseSettings, SettingsConfigDict


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
