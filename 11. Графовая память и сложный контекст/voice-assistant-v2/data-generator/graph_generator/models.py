"""Доменная модель и плоское графовое представление."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class Company:
    """Компания из справочника"""

    name: str


@dataclass(frozen=True, slots=True)
class Technology:
    """Технология из справочника, зависимости заданы именами"""

    name: str
    category: str
    depends_on: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Person:
    """Сотрудник, ссылается на компанию и известные ему технологии"""

    name: str
    email: str
    position: str
    experience: int
    company: Company
    skills: tuple[Technology, ...]


@dataclass(frozen=True, slots=True)
class Project:
    """Проект с владельцем, командой и технологическим стеком"""

    name: str
    type: str
    status: str
    owner: Company
    team: tuple[Person, ...]
    stack: tuple[Technology, ...]


# доменные сущности, на которые могут ссылаться фрагменты текста
Entity = Company | Technology | Person | Project


@dataclass(frozen=True, slots=True)
class Chunk:
    """Фрагмент документа с текстом и упомянутыми сущностями"""

    text: str
    order: int
    mentions: tuple[Entity, ...]


@dataclass(frozen=True, slots=True)
class Document:
    """Документ лексического графа, состоит из фрагментов"""

    title: str
    kind: str
    chunks: tuple[Chunk, ...]


@dataclass(frozen=True, slots=True)
class Dataset:
    """Полный результат генерации в виде объектной модели"""

    companies: tuple[Company, ...]
    technologies: tuple[Technology, ...]
    persons: tuple[Person, ...]
    projects: tuple[Project, ...]
    documents: tuple[Document, ...]


@dataclass(frozen=True, slots=True)
class Node:
    """Узел графа в плоском представлении"""

    id: int
    label: str
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Relation:
    """Направленная связь между двумя узлами по их id"""

    start: int
    type: str
    end: int
