"""Наполнение базы знаний в Neo4j данными из генератора

Сам граф (люди, компании, технологии, проекты, документы и фрагменты)
делает пакет graph_generator из папки data-generator - это подкинутый
проект, который взят за основу наполнения. Скрипт лишь добавляет к
нему то, что нужно гибридному поиску: считает эмбеддинги фрагментов
и создает векторный индекс поверх поля embedding.

Запуск из корня проекта:

    docker compose up -d
    python seed_knowledge.py

Скрипт начинает с полной очистки графа, поэтому его можно спокойно
запускать повторно - полусостояний после сбоя не остается.

Важно: тексты документов сознательно не проговаривают связи WORKS_AT,
WORKS_ON, OWNED_BY и USES. Кто где работает и какой проект что
использует, знает только граф - на таких вопросах и виден выигрыш
гибридного поиска перед чисто векторным
"""

import logging
import sys
from collections.abc import Callable
from pathlib import Path

from app.config import Settings, normalize_proxy_env
from app.embeddings import Embedder
from app.knowledge import VECTOR_INDEX_NAME

# пакет генератора лежит в папке data-generator, имя с дефисом не
# дает импортировать ее как пакет, поэтому добавляем в sys.path
sys.path.insert(
    0, str(Path(__file__).resolve().parent / "data-generator")
)

from graph_generator.generator import GraphGenerator  # noqa: E402
from graph_generator.graph import build_graph  # noqa: E402
from graph_generator.models import Node, Relation  # noqa: E402

# экспортеру нужен драйвер neo4j, но импорт мягкий, чтобы модуль
# можно было импортировать в тестах без группы rag
try:
    from graph_generator.exporter import Neo4jExporter
except ImportError:
    Neo4jExporter = None

logger = logging.getLogger(__name__)


def attach_embeddings(
    nodes: list[Node], encode: Callable[[str], list[float]]
) -> list[Node]:
    """Возвращает новый список узлов с векторами у фрагментов

    Функция чистая - входной список и сами узлы не меняются, для
    каждого Chunk создается копия с добавленым полем embedding
    """
    result: list[Node] = []
    for node in nodes:
        if node.label != "Chunk":
            result.append(node)
            continue
        properties = dict(node.properties)
        properties["embedding"] = encode(properties["text"])
        result.append(
            Node(id=node.id, label=node.label, properties=properties)
        )
    return result


def embedding_dimensions(nodes: list[Node]) -> int:
    """Достает размерность вектора из первого фрагмента"""
    for node in nodes:
        if node.label == "Chunk":
            return len(node.properties["embedding"])
    raise ValueError("в графе нет ни одного фрагмента с эмбеддингом")


def log_stats(nodes: list[Node], relations: list[Relation]) -> None:
    """Пишет в лог сводку по загруженному графу"""
    logger.info(
        "готово: %d узлов, %d связей", len(nodes), len(relations)
    )
    counts: dict[str, int] = {}
    for node in nodes:
        counts[node.label] = counts.get(node.label, 0) + 1
    for label in sorted(counts):
        logger.info("  %s: %d", label, counts[label])


def main() -> None:
    """Точка входа - генерация, эмбеддинги, загрузка в Neo4j"""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    # системный прокси вида socks:// чиним до похода за моделью
    normalize_proxy_env()
    if Neo4jExporter is None:
        raise SystemExit(
            "Нужен драйвер neo4j, поставьте зависимости: "
            "pip install -e \".[rag]\""
        )
    settings = Settings()
    embedder = Embedder(settings)
    if not embedder.available:
        raise SystemExit(
            "Нужен sentence-transformers, поставьте зависимости: "
            "pip install -e \".[rag]\""
        )

    dataset = GraphGenerator().generate()
    nodes, relations = build_graph(dataset)

    total_chunks = sum(1 for node in nodes if node.label == "Chunk")
    logger.info("считаем эмбеддинги для %d фрагментов", total_chunks)
    nodes = attach_embeddings(nodes, embedder.embed_passage)
    dimensions = embedding_dimensions(nodes)

    exporter = Neo4jExporter(
        settings.neo4j_uri,
        settings.neo4j_username,
        settings.neo4j_password,
        database=settings.neo4j_database,
    )
    with exporter:
        exporter.erase_all()
        exporter.export(nodes, relations)
        exporter.create_vector_index(
            VECTOR_INDEX_NAME, "Chunk", "embedding", dimensions
        )

    log_stats(nodes, relations)


if __name__ == "__main__":
    main()
