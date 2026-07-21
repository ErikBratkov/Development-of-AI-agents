"""Общие фикстуры для тестов генератора."""

import pytest

from graph_generator.generator import GraphGenerator
from graph_generator.graph import build_graph
from graph_generator.models import Dataset, Node, Relation


@pytest.fixture(scope="session")
def dataset() -> Dataset:
    """Набор данных, сгенерированный с фиксированным сидом"""
    return GraphGenerator(seed=42).generate()


@pytest.fixture(scope="session")
def graph(dataset: Dataset) -> tuple[list[Node], list[Relation]]:
    """Плоское графовое представление того же набора данных"""
    return build_graph(dataset)
