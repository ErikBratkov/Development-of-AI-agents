from app.memory import DialogueMemory, Turn, estimate_tokens


def test_estimate_tokens_minimum() -> None:
    """Оценка не бывает меньше одного токена даже для пустой строки"""
    assert estimate_tokens("") == 1
    assert estimate_tokens("ab") == 1


def test_estimate_tokens_grows_with_text() -> None:
    """Чем длиннее текст, тем больше оценка"""
    short = estimate_tokens("привет")
    longer = estimate_tokens("привет, как у тебя дела сегодня вечером")
    assert longer > short


def test_build_messages_without_summary(memory: DialogueMemory) -> None:
    """Без summary контекст - это system prompt и реплики по порядку"""
    memory.add_user("привет")
    memory.add_assistant("здравствуйте")
    messages = memory.build_messages()
    assert messages[0] == {
        "role": "system",
        "content": "Ты тестовый ассистент",
    }
    assert messages[1] == {"role": "user", "content": "привет"}
    assert messages[2] == {"role": "assistant", "content": "здравствуйте"}


def test_build_messages_with_summary(memory: DialogueMemory) -> None:
    """Summary подмешивается в system сообщение, а не отдельной репликой"""
    memory.summary = "говорили о погоде"
    memory.add_user("что дальше?")
    messages = memory.build_messages()
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert "говорили о погоде" in messages[0]["content"]


def test_needs_summary_false_for_short_history(
    memory: DialogueMemory,
) -> None:
    """Пока реплик мало, сжатие не запускается даже при большом объеме"""
    memory.add_user("д" * 1000)
    assert not memory.needs_summary()


def test_needs_summary_true_when_over_budget(
    memory: DialogueMemory,
) -> None:
    """Длинная история сверх бюджета токенов требует сжатия"""
    for _ in range(3):
        memory.add_user("д" * 200)
        memory.add_assistant("о" * 200)
    assert memory.needs_summary()


def test_split_for_summary_keeps_last_pairs(memory: DialogueMemory) -> None:
    """В хвосте остаются последние keep_last_turns пар реплик"""
    for index in range(6):
        memory.add_user(f"вопрос {index}")
        memory.add_assistant(f"ответ {index}")
    old, recent = memory.split_for_summary()
    assert len(recent) == 4
    assert recent[-1].text == "ответ 5"
    assert old[0].text == "вопрос 0"
    # исходная история при этом не меняется
    assert len(memory.turns) == 12


def test_apply_summary_replaces_previous(memory: DialogueMemory) -> None:
    """Новое резюме замещает старое, история режется до хвоста"""
    memory.summary = "старое резюме"
    recent = [Turn(role="user", text="последний вопрос")]
    memory.apply_summary("новое резюме", recent)
    assert memory.summary == "новое резюме"
    assert memory.turns == recent
    # хранится копия списка, а не сам аргумент
    assert memory.turns is not recent
