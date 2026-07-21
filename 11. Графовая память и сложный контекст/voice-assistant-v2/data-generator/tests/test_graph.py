"""Тесты адаптера объектной модели в граф."""

from graph_generator import config
from graph_generator.models import Dataset, Node, Relation


def test_node_ids_unique(graph: tuple[list[Node], list[Relation]]) -> None:
    """Идентификаторы узлов не повторяются"""
    nodes, _ = graph
    ids = [node.id for node in nodes]

    assert len(ids) == len(set(ids))


def test_relations_reference_existing_nodes(
    graph: tuple[list[Node], list[Relation]],
) -> None:
    """Каждая связь ссылается на существующие узлы"""
    nodes, relations = graph
    known_ids = {node.id for node in nodes}

    for rel in relations:
        assert rel.start in known_ids
        assert rel.end in known_ids


def test_depends_on_taken_from_catalog(
    graph: tuple[list[Node], list[Relation]],
) -> None:
    """Число связей DEPENDS_ON совпадает со справочником"""
    _, relations = graph
    depends = [r for r in relations if r.type == "DEPENDS_ON"]
    expected = sum(len(item["depends_on"]) for item in config.TECHNOLOGIES)

    assert len(depends) == expected


def test_relation_counts_match_model(
    dataset: Dataset, graph: tuple[list[Node], list[Relation]]
) -> None:
    """Количество связей соответствует объектной модели"""
    _, relations = graph

    works_at = [r for r in relations if r.type == "WORKS_AT"]
    knows = [r for r in relations if r.type == "KNOWS"]
    works_on = [r for r in relations if r.type == "WORKS_ON"]

    assert len(works_at) == len(dataset.persons)
    assert len(knows) == sum(len(p.skills) for p in dataset.persons)
    assert len(works_on) == sum(len(p.team) for p in dataset.projects)


def test_person_node_properties(
    graph: tuple[list[Node], list[Relation]],
) -> None:
    """Узлы сотрудников содержат все нужные свойства"""
    nodes, _ = graph
    persons = [n for n in nodes if n.label == "Person"]

    assert persons
    for node in persons:
        assert set(node.properties) == {
            "name", "email", "position", "experience",
        }
