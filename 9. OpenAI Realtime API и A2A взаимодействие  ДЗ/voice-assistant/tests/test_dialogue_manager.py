from app.dialogue_manager import format_turns_for_summary, split_sentences
from app.memory import Turn


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
