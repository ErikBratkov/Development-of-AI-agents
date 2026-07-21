"""Тесты генерации объектной модели."""

from graph_generator import config
from graph_generator.generator import GraphGenerator
from graph_generator.models import Dataset


def test_catalog_entities_created_once(dataset: Dataset) -> None:
    """Справочники переносятся в модель один в один, без дублей"""
    tech_names = [t.name for t in dataset.technologies]
    company_names = [c.name for c in dataset.companies]

    assert tech_names == [item["name"] for item in config.TECHNOLOGIES]
    assert company_names == list(config.COMPANIES)


def test_generation_is_reproducible() -> None:
    """Один и тот же сид дает одинаковые данные"""
    first = GraphGenerator(seed=7).generate()
    second = GraphGenerator(seed=7).generate()

    assert first == second


def test_seed_changes_result() -> None:
    """Разные сиды дают разных сотрудников"""
    first = GraphGenerator(seed=1).generate()
    second = GraphGenerator(seed=2).generate()

    assert first.persons != second.persons


def test_person_attributes_in_bounds(dataset: Dataset) -> None:
    """Атрибуты сотрудников не выходят за рамки конфига"""
    low, high = config.EXPERIENCE_RANGE

    for person in dataset.persons:
        assert low <= person.experience <= high
        assert person.position in config.POSITIONS
        assert len(person.skills) == len(set(person.skills))


def test_project_names_unique(dataset: Dataset) -> None:
    """Имена проектов не повторяются"""
    names = [p.name for p in dataset.projects]

    assert len(names) == len(set(names))


def test_project_stack_matches_type(dataset: Dataset) -> None:
    """Стек проекта содержит только разрешенные категории"""
    for project in dataset.projects:
        allowed = config.PROJECT_TYPE_CATEGORIES[project.type]
        for tech in project.stack:
            assert tech.category in allowed


def test_team_works_at_owner_company(dataset: Dataset) -> None:
    """Команда проекта набрана из сотрудников компании владельца"""
    for project in dataset.projects:
        for member in project.team:
            assert member.company == project.owner
