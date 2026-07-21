"""Тесты лексического слоя: документы, фрагменты и их связи."""

from graph_generator.models import Dataset, Node, Relation

DOMAIN_LABELS = {"Company", "Technology", "Person", "Project"}


def test_every_entity_has_document(dataset: Dataset) -> None:
    """На каждый проект и сотрудника создается свой документ"""
    expected = len(dataset.projects) + len(dataset.persons)

    assert len(dataset.documents) == expected


def test_chunks_are_ordered(dataset: Dataset) -> None:
    """Фрагменты внутри документа пронумерованы подряд с нуля"""
    for document in dataset.documents:
        assert document.chunks
        for position, chunk in enumerate(document.chunks):
            assert chunk.order == position
            assert chunk.text.strip()


def test_mentions_point_to_dataset_entities(dataset: Dataset) -> None:
    """Упоминания ссылаются на сущности из этого же набора"""
    known = {
        id(obj)
        for obj in (
            dataset.companies
            + dataset.technologies
            + dataset.persons
            + dataset.projects
        )
    }

    for document in dataset.documents:
        for chunk in document.chunks:
            assert chunk.mentions
            for entity in chunk.mentions:
                assert id(entity) in known


def test_mention_names_present_in_text(dataset: Dataset) -> None:
    """Имя каждой упомянутой сущности встречается в тексте"""
    for document in dataset.documents:
        for chunk in document.chunks:
            for entity in chunk.mentions:
                assert entity.name in chunk.text


def test_lexical_relation_counts(
    dataset: Dataset, graph: tuple[list[Node], list[Relation]]
) -> None:
    """Число PART_OF, NEXT_CHUNK и FIRST_CHUNK сходится с моделью"""
    _, relations = graph
    total_chunks = sum(len(d.chunks) for d in dataset.documents)

    part_of = [r for r in relations if r.type == "PART_OF"]
    next_chunk = [r for r in relations if r.type == "NEXT_CHUNK"]
    first_chunk = [r for r in relations if r.type == "FIRST_CHUNK"]

    assert len(part_of) == total_chunks
    assert len(next_chunk) == total_chunks - len(dataset.documents)
    assert len(first_chunk) == len(dataset.documents)


def test_mentions_connect_chunks_to_domain(
    graph: tuple[list[Node], list[Relation]],
) -> None:
    """Связи MENTIONS идут от фрагментов к доменным узлам"""
    nodes, relations = graph
    label_by_id = {node.id: node.label for node in nodes}
    mentions = [r for r in relations if r.type == "MENTIONS"]

    assert mentions
    for rel in mentions:
        assert label_by_id[rel.start] == "Chunk"
        assert label_by_id[rel.end] in DOMAIN_LABELS


def test_chunk_node_properties(
    graph: tuple[list[Node], list[Relation]],
) -> None:
    """Узлы фрагментов содержат текст и порядковый номер"""
    nodes, _ = graph
    chunks = [n for n in nodes if n.label == "Chunk"]

    assert chunks
    for node in chunks:
        assert set(node.properties) == {"text", "order"}
