import pytest

from app.memory import DialogueMemory


@pytest.fixture
def memory() -> DialogueMemory:
    """Память диалога с маленькими лимитами, чтобы тестам было проще"""
    return DialogueMemory(
        system_prompt="Ты тестовый ассистент",
        keep_last_turns=2,
        summarize_trigger_tokens=50,
    )
