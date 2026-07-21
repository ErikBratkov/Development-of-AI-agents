import pytest

import seed_knowledge as seed
from graph_generator.models import Node


def fake_encode(text: str) -> list[float]:
    """Детерминированый вектор вместо настоящей модели"""
    return [float(len(text)), 0.5]


def make_nodes() -> list[Node]:
    """Маленький граф из узлов разных меток для проверок"""
    return [
        Node(id=1, label="Person", properties={"name": "Alice"}),
        Node(id=2, label="Chunk", properties={"text": "короткий", "order": 0}),
        Node(id=3, label="Chunk", properties={"text": "текст подлиннее",
                                              "order": 1}),
    ]


def test_attach_embeddings_only_chunks() -> None:
    """Векторы добавляются только фрагментам, остальные узлы как были"""
    nodes = make_nodes()
    result = seed.attach_embeddings(nodes, fake_encode)
    assert "embedding" not in result[0].properties
    assert result[1].properties["embedding"] == [8.0, 0.5]
    assert result[2].properties["embedding"] == [15.0, 0.5]


def test_attach_embeddings_keeps_other_properties() -> None:
    """Прежние свойства фрагмента не теряются при добавлении вектора"""
    result = seed.attach_embeddings(make_nodes(), fake_encode)
    assert result[1].properties["text"] == "короткий"
    assert result[1].properties["order"] == 0


def test_attach_embeddings_does_not_mutate_input() -> None:
    """Функция чистая - входные узлы не трогаются"""
    nodes = make_nodes()
    seed.attach_embeddings(nodes, fake_encode)
    assert "embedding" not in nodes[1].properties
    assert "embedding" not in nodes[2].properties


def test_embedding_dimensions() -> None:
    """Размерность берется из первого фрагмента с вектором"""
    nodes = seed.attach_embeddings(make_nodes(), fake_encode)
    assert seed.embedding_dimensions(nodes) == 2


def test_embedding_dimensions_without_chunks() -> None:
    """Граф без фрагментов - это ошибка наполнения, а не тихий ноль"""
    nodes = [Node(id=1, label="Person", properties={"name": "Bob"})]
    with pytest.raises(ValueError):
        seed.embedding_dimensions(nodes)
