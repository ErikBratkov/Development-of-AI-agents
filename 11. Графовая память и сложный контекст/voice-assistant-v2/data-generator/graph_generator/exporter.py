"""Экспорт графа в Neo4j обычным Cypher, без APOC.

Метки и типы связей известны заранее, поэтому на каждую группу
собирается один статический запрос. Выгрузка идемпотентна за счет
MERGE - при сбое ее можно просто запустить еще раз.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterator
from types import TracebackType
from typing import Any

from neo4j import GraphDatabase, ManagedTransaction
from neo4j.exceptions import ServiceUnavailable, SessionExpired

from graph_generator.models import Node, Relation

# имена меток и типов связей попадают в текст запроса, поэтому
# пропускаем только безопасные идентификаторы
NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _check_name(name: str) -> str:
    """Проверяет что имя метки или связи безопасно для запроса"""
    if not NAME_PATTERN.match(name):
        raise ValueError(f"недопустимое имя в графе: {name!r}")
    return name


def _chunks(rows: list[dict], size: int) -> Iterator[list[dict]]:
    """Режет список на батчи фиксированного размера"""
    for start in range(0, len(rows), size):
        yield rows[start:start + size]


def _run_batch(
    tx: ManagedTransaction, query: str, rows: list[dict]
) -> int:
    """Выполняет запрос над батчем и возвращает счетчик done"""
    record = tx.run(query, rows=rows).single()
    return int(record["done"])


class Neo4jExporter:
    """Пишет узлы и связи в Neo4j батчами."""

    def __init__(
        self,
        uri: str,
        username: str,
        password: str,
        batch_size: int = 1000,
        retries: int = 3,
        retry_delay: float = 1.0,
        database: str | None = None,
    ) -> None:
        """Открывает драйвер и запоминает настройки повторов"""
        self._driver = GraphDatabase.driver(uri, auth=(username, password))
        self._batch_size = batch_size
        self._retries = retries
        self._retry_delay = retry_delay
        self._database = database

    def __enter__(self) -> Neo4jExporter:
        """Дает использовать экспортер как контекстный менеджер"""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Закрывает драйвер при выходе из блока with"""
        self.close()

    def close(self) -> None:
        """Закрывает соединение с базой"""
        self._driver.close()

    def export(self, nodes: list[Node], relations: list[Relation]) -> None:
        """Полный цикл выгрузки, безопасен для повторного запуска"""
        labels = sorted({node.label for node in nodes})
        self._create_constraints(labels)
        self._export_nodes(nodes)
        self._export_relations(nodes, relations)

    def _retry(self, func: Callable[..., Any], *args: Any) -> Any:
        """Повторяет операцию при обрыве соединения с базой"""
        last_error: Exception | None = None
        for attempt in range(1, self._retries + 1):
            try:
                return func(*args)
            except (ServiceUnavailable, SessionExpired) as error:
                last_error = error
                if attempt < self._retries:
                    time.sleep(self._retry_delay * attempt)
        raise last_error

    def erase_all(self) -> None:
        """Полностью очищает граф перед загрузкой с нуля"""
        self._retry(self._run_plain, "MATCH (n) DETACH DELETE n")

    def create_vector_index(
        self, name: str, label: str, prop: str, dimensions: int
    ) -> None:
        """Создает векторный индекс по свойству узлов метки

        Параметры в schema-командах не поддерживаются, поэтому все
        имена проверяются и подставляются в текст запроса
        """
        dims = int(dimensions)
        if dims < 1:
            raise ValueError("размерность вектора должна быть больше нуля")
        query = (
            f"CREATE VECTOR INDEX `{_check_name(name)}` IF NOT EXISTS "
            f"FOR (n:`{_check_name(label)}`) ON (n.`{_check_name(prop)}`) "
            "OPTIONS {indexConfig: {"
            f"`vector.dimensions`: {dims}, "
            "`vector.similarity_function`: 'cosine'}}"
        )
        self._retry(self._run_plain, query)

    def _run_plain(self, query: str) -> None:
        """Выполняет одиночный запрос вне явной транзакции"""
        with self._driver.session(database=self._database) as session:
            session.run(query).consume()

    def _write_batch(self, query: str, rows: list[dict]) -> int:
        """Пишет один батч в отдельной управляемой транзакции"""
        with self._driver.session(database=self._database) as session:
            return session.execute_write(_run_batch, query, rows)

    def _create_constraints(self, labels: list[str]) -> None:
        """Создает ограничение уникальности id для каждой метки"""
        for label in labels:
            query = (
                "CREATE CONSTRAINT IF NOT EXISTS "
                f"FOR (n:`{_check_name(label)}`) "
                "REQUIRE n.id IS UNIQUE"
            )
            self._retry(self._run_plain, query)

    def _export_nodes(self, nodes: list[Node]) -> None:
        """Выгружает узлы, группируя их по метке"""
        by_label: dict[str, list[dict]] = {}
        for node in nodes:
            row = {"id": node.id, "props": node.properties}
            by_label.setdefault(node.label, []).append(row)

        for label, rows in by_label.items():
            query = (
                "UNWIND $rows AS row\n"
                f"MERGE (n:`{_check_name(label)}` {{id: row.id}})\n"
                "SET n += row.props\n"
                "RETURN count(*) AS done"
            )
            for batch in _chunks(rows, self._batch_size):
                self._retry(self._write_batch, query, batch)

    def _export_relations(
        self, nodes: list[Node], relations: list[Relation]
    ) -> None:
        """Выгружает связи, группируя по типу и меткам концов"""
        label_by_id = {node.id: node.label for node in nodes}

        grouped: dict[tuple[str, str, str], list[dict]] = {}
        for rel in relations:
            key = (label_by_id[rel.start], rel.type, label_by_id[rel.end])
            grouped.setdefault(key, []).append(
                {"start": rel.start, "end": rel.end}
            )

        for (start_label, rel_type, end_label), rows in grouped.items():
            query = (
                "UNWIND $rows AS row\n"
                f"MATCH (a:`{_check_name(start_label)}` "
                "{id: row.start})\n"
                f"MATCH (b:`{_check_name(end_label)}` {{id: row.end}})\n"
                f"MERGE (a)-[:`{_check_name(rel_type)}`]->(b)\n"
                "RETURN count(*) AS done"
            )
            done = 0
            for batch in _chunks(rows, self._batch_size):
                done += self._retry(self._write_batch, query, batch)

            # если совпало меньше строк чем отправили, значит часть
            # узлов не нашлась - молча терять связи нельзя
            if done != len(rows):
                raise RuntimeError(
                    f"для {rel_type} создано {done} связей "
                    f"из {len(rows)}, проверьте выгрузку узлов"
                )
