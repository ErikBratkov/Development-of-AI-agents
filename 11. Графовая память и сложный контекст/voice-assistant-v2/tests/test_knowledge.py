import asyncio
from typing import Any

import pytest

import app.knowledge as knowledge_module
from app.config import Settings
from app.knowledge import (
    AGGREGATE_TOOL_SPEC,
    KNOWLEDGE_TOOL_SPEC,
    KnowledgeBase,
    build_count_query,
    build_experience_query,
    build_facts_query,
    build_list_query,
    format_aggregate_result,
    format_fact,
    format_knowledge_context,
    warmup_knowledge_base,
)


class FakeEmbedder:
    """Подменяет модель эмбеддингов, вектор всегда один и тот же"""

    def __init__(self, available: bool = True) -> None:
        self.available = available
        self.warmed_up = False

    def embed_query(self, text: str) -> list[float]:
        return [0.1, 0.2, 0.3]

    def warmup(self) -> None:
        self.warmed_up = True


class FakeSession:
    """Сессия, отдающая заранее заготовленые пачки результатов"""

    def __init__(self, batches: list[Any]) -> None:
        self._batches = batches

    async def __aenter__(self) -> "FakeSession":
        return self

    async def __aexit__(self, *args: Any) -> bool:
        return False

    async def execute_read(self, fn: Any, *args: Any) -> Any:
        result = self._batches.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class FakeDriver:
    """Драйвер, выдающий одну общую очередь результатов"""

    def __init__(self, batches: list[Any]) -> None:
        self._batches = batches

    def session(self, database: str) -> FakeSession:
        return FakeSession(self._batches)


def make_kb(settings: Settings, batches: list[Any]) -> KnowledgeBase:
    """База знаний с фейковыми эмбеддером и драйвером"""
    return KnowledgeBase(
        settings,
        embedder=FakeEmbedder(),
        driver=FakeDriver(batches),
    )


def test_spec_matches_handler_name() -> None:
    """Имя в спеке совпадает с ключом обработчика"""
    assert KNOWLEDGE_TOOL_SPEC["function"]["name"] == "search_knowledge"


def test_format_fact() -> None:
    """Факт собирается в читаемую стрелочную запись"""
    row = {"subject": "Alice Adams", "relation": "WORKS_AT", "object": "Acme"}
    assert format_fact(row) == "Alice Adams -[WORKS_AT]-> Acme"


def test_format_knowledge_context_full() -> None:
    """Сначала факты, потом выдержки, между блоками пустая строка"""
    facts = [
        {"subject": "Alice", "relation": "KNOWS", "object": "Neo4j"},
    ]
    chunks = [{"title": "Alice profile", "text": "Alice - инженер."}]
    context = format_knowledge_context(facts, chunks)
    lines = context.split("\n")
    assert lines[0] == "Факты из графа знаний:"
    assert lines[1] == "- Alice -[KNOWS]-> Neo4j"
    assert lines[2] == ""
    assert lines[3] == "Выдержки из базы знаний:"
    assert lines[4] == "- Alice profile: Alice - инженер."


def test_format_knowledge_context_empty() -> None:
    """Без фактов и фрагментов возвращается пустая строка"""
    assert format_knowledge_context([], []) == ""


def test_format_knowledge_context_does_not_mutate_args() -> None:
    """Функция чистая - входные списки не меняются"""
    facts = [{"subject": "a", "relation": "R", "object": "b"}]
    chunks = [{"title": "t", "text": "x"}]
    facts_copy = [dict(row) for row in facts]
    chunks_copy = [dict(row) for row in chunks]
    format_knowledge_context(facts, chunks)
    assert facts == facts_copy
    assert chunks == chunks_copy


def test_build_facts_query_bakes_hops() -> None:
    """Глубина обхода подставляется в текст запроса"""
    query = build_facts_query(2)
    assert "*1..2" in query
    assert "$chunk_ids" in query
    assert "$max_facts" in query
    assert "MENTIONS" in query


def test_build_facts_query_rejects_bad_hops() -> None:
    """Нулевая и слишком большая глубина отклоняются"""
    with pytest.raises(ValueError):
        build_facts_query(0)
    with pytest.raises(ValueError):
        build_facts_query(6)


def test_search_returns_context(settings: Settings) -> None:
    """Успешный поиск склеивает факты и выдержки в один текст"""
    chunks = [{"id": 100, "title": "Alice profile", "text": "текст"}]
    facts = [{"subject": "Alice", "relation": "WORKS_AT", "object": "Acme"}]
    kb = make_kb(settings, [chunks, facts])
    answer = asyncio.run(kb.search("кто работает в Acme"))
    assert "Alice -[WORKS_AT]-> Acme" in answer
    assert "Alice profile" in answer


def test_search_empty_result(settings: Settings) -> None:
    """Пустая выдача превращается в честный текст, а не пустую строку"""
    kb = make_kb(settings, [[]])
    answer = asyncio.run(kb.search("что-то незнакомое"))
    assert answer == "В базе знаний ничего не найдено по этому вопросу"


def test_search_blank_question(settings: Settings) -> None:
    """Пустой вопрос не ходит ни в модель, ни в базу"""
    kb = make_kb(settings, [])
    answer = asyncio.run(kb.search("   "))
    assert "пустой" in answer.lower()


def test_search_db_error_becomes_text(settings: Settings) -> None:
    """Сбой запроса к базе отдается модели текстом, не исключением"""
    kb = make_kb(settings, [RuntimeError("боль")])
    answer = asyncio.run(kb.search("кто знает Python"))
    assert answer == "База знаний сейчас недоступна, попробуйте позже"


def test_search_without_embedder(settings: Settings) -> None:
    """Без установленой группы rag инструмент вежливо отказывает"""
    kb = KnowledgeBase(
        settings, embedder=FakeEmbedder(available=False), driver=object()
    )
    answer = asyncio.run(kb.search("кто знает Python"))
    assert "не установлена" in answer


def test_warmup_knowledge_base(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Прогрев доходит до эмбеддера, когда база доступна"""
    kb = make_kb(settings, [])
    monkeypatch.setattr(knowledge_module, "_knowledge_base", kb)
    asyncio.run(warmup_knowledge_base())
    assert kb._embedder.warmed_up


def test_warmup_knowledge_base_unavailable(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Без установленых зависимостей прогрев тихо пропускается"""
    kb = KnowledgeBase(
        settings, embedder=FakeEmbedder(available=False), driver=object()
    )
    monkeypatch.setattr(knowledge_module, "_knowledge_base", kb)
    asyncio.run(warmup_knowledge_base())
    assert not kb._embedder.warmed_up


def test_begin_warmup_toggles_pending(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Пока идет прогрев, warmup_pending горит, после - сбрасывается"""
    kb = make_kb(settings, [])
    monkeypatch.setattr(knowledge_module, "_knowledge_base", kb)

    async def scenario() -> tuple[bool, bool]:
        task = knowledge_module.begin_warmup()
        pending_during = knowledge_module.warmup_pending()
        await task
        # после прогрева ожидание должно вернуться мгновенно
        await knowledge_module.wait_warmup()
        return pending_during, knowledge_module.warmup_pending()

    during, after = asyncio.run(scenario())
    assert during
    assert not after


def test_aggregate_spec_matches_handler_name() -> None:
    """Имя в спеке агрегации совпадает с ключом обработчика"""
    name = AGGREGATE_TOOL_SPEC["function"]["name"]
    assert name == "aggregate_knowledge"


def test_build_list_query_uses_whitelisted_label() -> None:
    """Метка и дополнительное поле подставляются из белых списков"""
    query = build_list_query("person")
    assert "MATCH (n:Person)" in query
    assert "n.position AS extra" in query
    # у компании дополнительного поля нет
    assert "extra" not in build_list_query("company")
    with pytest.raises(ValueError):
        build_list_query("planet")


def test_build_count_query() -> None:
    """Подсчет идет по всем узлам метки без всяких top_k"""
    query = build_count_query("project")
    assert "MATCH (n:Project)" in query
    assert "count(n) AS total" in query
    with pytest.raises(ValueError):
        build_count_query("planet")


def test_build_experience_query() -> None:
    """Запрос экстремума собирает нужную функцию агрегации

    Отсутствующий опыт приравнивается к нулю, люди без заполненного
    поля не должны молча выпадать из ответа
    """
    min_query = build_experience_query("min_experience")
    max_query = build_experience_query("max_experience")
    assert "min(coalesce(p.experience, 0))" in min_query
    assert "max(coalesce(p.experience, 0))" in max_query
    assert "WHERE coalesce(p.experience, 0) = target" in min_query
    with pytest.raises(ValueError):
        build_experience_query("avg_experience")


def test_format_aggregate_count() -> None:
    """Подсчет превращается в короткую строку с числом"""
    text = format_aggregate_result("person", "count", [{"total": 25}])
    assert "25" in text
    assert "сотрудников" in text


def test_format_aggregate_list() -> None:
    """Список содержит все строки с именем и дополнительным полем"""
    rows = [
        {"name": "Alice Adams", "extra": "QA engineer"},
        {"name": "Bob Brown", "extra": None},
    ]
    text = format_aggregate_result("person", "list", rows)
    lines = text.split("\n")
    assert "всего сотрудников 2" in lines[0]
    assert lines[1] == "- Alice Adams (QA engineer)"
    assert lines[2] == "- Bob Brown"


def test_format_aggregate_experience() -> None:
    """Экстремум опыта показывает всех людей с этим значением"""
    rows = [
        {"name": "Alice", "extra": "QA", "experience": 1},
        {"name": "Bob", "extra": "Dev", "experience": 1},
    ]
    text = format_aggregate_result("person", "min_experience", rows)
    assert "наименьшим" in text
    assert text.count("опыт в годах: 1") == 2


def test_format_aggregate_empty_list() -> None:
    """Пустая выдача превращается в честный текст"""
    text = format_aggregate_result("project", "list", [])
    assert "нет ни одной записи" in text


def test_aggregate_returns_full_list(settings: Settings) -> None:
    """Успешная агрегация отдает готовый текст со всеми строками"""
    rows = [{"name": "Neo4j", "extra": "database"}]
    kb = make_kb(settings, [rows])
    answer = asyncio.run(kb.aggregate("technology", "list"))
    assert "Neo4j (database)" in answer


def test_aggregate_rejects_unknown_values(settings: Settings) -> None:
    """Неизвестные сущность и операция не доходят до базы"""
    kb = make_kb(settings, [])
    assert "Неизвестный тип" in asyncio.run(
        kb.aggregate("planet", "list")
    )
    assert "Неизвестная операция" in asyncio.run(
        kb.aggregate("person", "drop_all")
    )


def test_aggregate_experience_only_for_person(
    settings: Settings,
) -> None:
    """Экстремум опыта для компаний вежливо отклоняется"""
    kb = make_kb(settings, [])
    answer = asyncio.run(kb.aggregate("company", "min_experience"))
    assert "только у сотрудников" in answer


def test_aggregate_db_error_becomes_text(settings: Settings) -> None:
    """Сбой запроса отдается модели текстом, не исключением"""
    kb = make_kb(settings, [RuntimeError("боль")])
    answer = asyncio.run(kb.aggregate("person", "count"))
    assert answer == "База знаний сейчас недоступна, попробуйте позже"


def test_aggregate_works_without_embedder(settings: Settings) -> None:
    """Точным запросам модель эмбеддингов не нужна"""
    kb = KnowledgeBase(
        settings,
        embedder=FakeEmbedder(available=False),
        driver=FakeDriver([[{"total": 3}]]),
    )
    answer = asyncio.run(kb.aggregate("company", "count"))
    assert "3" in answer


def test_aggregate_without_driver(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Без драйвера neo4j агрегация вежливо отказывает"""
    monkeypatch.setattr(knowledge_module, "AsyncGraphDatabase", None)
    kb = KnowledgeBase(settings, embedder=FakeEmbedder(), driver=None)
    answer = asyncio.run(kb.aggregate("person", "list"))
    assert "не установлена" in answer


def test_search_skips_facts_when_no_chunks(settings: Settings) -> None:
    """Без найденных фрагментов второй запрос к базе не выполняется"""
    batches: list[Any] = [[]]
    kb = make_kb(settings, batches)
    asyncio.run(kb.search("вопрос"))
    # очередь опустела ровно на один результат, второго чтения не было
    assert batches == []
