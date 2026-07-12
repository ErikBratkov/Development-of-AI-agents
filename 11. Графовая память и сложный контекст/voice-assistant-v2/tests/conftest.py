import sys
from pathlib import Path

import pytest

# корень проекта в sys.path, чтобы seed_knowledge.py из корня
# был импортируем в тестах без установки пакета
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import Settings  # noqa: E402
from app.memory import DialogueMemory  # noqa: E402


@pytest.fixture
def memory() -> DialogueMemory:
    """Память диалога с маленькими лимитами, чтобы тестам было проще"""
    return DialogueMemory(
        system_prompt="Ты тестовый ассистент",
        keep_last_turns=2,
        summarize_trigger_tokens=50,
    )


@pytest.fixture
def settings() -> Settings:
    """Настройки с дефолтами, реальный .env не читается"""
    return Settings(_env_file=None)
