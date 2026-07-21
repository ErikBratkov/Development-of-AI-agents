"""Точка входа: генерация графа и выгрузка в Neo4j.

Параметры подключения берутся из переменных окружения
NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD. Если NEO4J_URI не задан,
скрипт только печатает статистику по сгенерированным данным.
"""

import os

from graph_generator.exporter import Neo4jExporter
from graph_generator.generator import GraphGenerator
from graph_generator.graph import build_graph
from graph_generator.models import Node, Relation


def print_stats(nodes: list[Node], relations: list[Relation]) -> None:
    """Печатает краткую сводку по построенному графу"""
    print(f"Узлов:  {len(nodes)}")
    print(f"Связей: {len(relations)}")
    print()

    label_stats: dict[str, int] = {}
    for node in nodes:
        label_stats[node.label] = label_stats.get(node.label, 0) + 1

    for label, count in sorted(label_stats.items()):
        print(f"  {label:12} {count}")
    print()

    type_stats: dict[str, int] = {}
    for rel in relations:
        type_stats[rel.type] = type_stats.get(rel.type, 0) + 1

    for rel_type, count in sorted(type_stats.items()):
        print(f"  {rel_type:12} {count}")


def main() -> None:
    """Генерирует данные и выгружает их, если задано подключение"""
    dataset = GraphGenerator().generate()
    nodes, relations = build_graph(dataset)
    print_stats(nodes, relations)
    print()

    uri = os.environ.get("NEO4J_URI")
    if not uri:
        print("NEO4J_URI не задан, экспорт в Neo4j пропущен")
        return

    username = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "")

    with Neo4jExporter(uri, username, password) as exporter:
        exporter.export(nodes, relations)
    print("Экспорт в Neo4j завершен")


if __name__ == "__main__":
    main()
