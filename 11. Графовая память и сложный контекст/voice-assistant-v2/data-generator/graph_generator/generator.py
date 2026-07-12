"""Генерация объектной модели предметной области.

Генератор ничего не знает про Neo4j и графы, он возвращает
обычные python обьекты со ссылками друг на друга.
"""

from random import Random

from faker import Faker

from graph_generator import config
from graph_generator.models import (
    Chunk,
    Company,
    Dataset,
    Document,
    Person,
    Project,
    Technology,
)


class GraphGenerator:
    """Собирает справочники и генерирует случайные экземпляры."""

    def __init__(self, seed: int = config.SEED) -> None:
        """Инициализирует источники случайности одним сидом"""
        self._rnd = Random(seed)
        self._fake = Faker("en_US")
        self._fake.seed_instance(seed)

    def generate(self) -> Dataset:
        """Возвращает набор данных, воспроизводимый по сиду"""
        companies = self._build_companies()
        technologies = self._build_technologies()
        persons = self._build_persons(companies, technologies)
        projects = self._build_projects(companies, persons, technologies)
        documents = self._build_documents(persons, projects)
        return Dataset(
            companies=companies,
            technologies=technologies,
            persons=persons,
            projects=projects,
            documents=documents,
        )

    @staticmethod
    def _build_companies() -> tuple[Company, ...]:
        """Создает компании строго по справочнику, без случайности"""
        return tuple(Company(name=name) for name in config.COMPANIES)

    @staticmethod
    def _build_technologies() -> tuple[Technology, ...]:
        """Создает технологии по справочнику и проверяет зависимости"""
        known = {item["name"] for item in config.TECHNOLOGIES}
        technologies = []
        for item in config.TECHNOLOGIES:
            for dep in item["depends_on"]:
                if dep not in known:
                    raise ValueError(
                        f"у технологии {item['name']!r} указана "
                        f"неизвестная зависимость {dep!r}"
                    )
            technologies.append(
                Technology(
                    name=item["name"],
                    category=item["category"],
                    depends_on=tuple(item["depends_on"]),
                )
            )
        return tuple(technologies)

    def _build_persons(
        self,
        companies: tuple[Company, ...],
        technologies: tuple[Technology, ...],
    ) -> tuple[Person, ...]:
        """Генерирует сотрудников со случайными атрибутами"""
        persons = []
        for _ in range(config.PERSON_COUNT):
            skills_count = self._rnd.randint(*config.SKILLS_PER_PERSON)
            persons.append(
                Person(
                    name=self._fake.name(),
                    email=self._fake.email(),
                    position=self._rnd.choice(config.POSITIONS),
                    experience=self._rnd.randint(*config.EXPERIENCE_RANGE),
                    company=self._rnd.choice(companies),
                    skills=tuple(
                        self._rnd.sample(technologies, skills_count)
                    ),
                )
            )
        return tuple(persons)

    def _build_projects(
        self,
        companies: tuple[Company, ...],
        persons: tuple[Person, ...],
        technologies: tuple[Technology, ...],
    ) -> tuple[Project, ...]:
        """Генерирует проекты с командой и подходящим стеком"""
        if config.PROJECT_COUNT > len(config.PROJECT_NAMES):
            raise ValueError(
                "имен в PROJECT_NAMES меньше чем требуется проектов"
            )

        names = self._rnd.sample(config.PROJECT_NAMES, config.PROJECT_COUNT)
        projects = []
        for name in names:
            owner = self._rnd.choice(companies)
            project_type = self._rnd.choice(
                list(config.PROJECT_TYPE_CATEGORIES)
            )

            allowed = config.PROJECT_TYPE_CATEGORIES[project_type]
            suitable = [t for t in technologies if t.category in allowed]
            stack_count = min(
                len(suitable), self._rnd.randint(*config.STACK_SIZE)
            )

            # команду набираем из сотрудников компании владельца,
            # так граф выглядит правдоподобней
            staff = [p for p in persons if p.company == owner]
            team_count = min(
                len(staff), self._rnd.randint(*config.TEAM_SIZE)
            )

            projects.append(
                Project(
                    name=name,
                    type=project_type,
                    status=self._rnd.choice(config.PROJECT_STATUSES),
                    owner=owner,
                    team=tuple(self._rnd.sample(staff, team_count)),
                    stack=tuple(self._rnd.sample(suitable, stack_count)),
                )
            )
        return tuple(projects)

    @classmethod
    def _build_documents(
        cls,
        persons: tuple[Person, ...],
        projects: tuple[Project, ...],
    ) -> tuple[Document, ...]:
        """Собирает синтетические документы по готовым фактам.

        Текст строится шаблонами из уже сгенерированных сущностей,
        поэтому упоминания известны точно и NER не требуется
        """
        documents = [cls._project_overview(p) for p in projects]
        documents += [cls._person_profile(p) for p in persons]
        return tuple(documents)

    @staticmethod
    def _project_overview(project: Project) -> Document:
        """Строит документ с описанием одного проекта

        Владелец, команда и стек в тексте сознательно не называются -
        связи OWNED_BY, WORKS_ON и USES живут только в графе, именно
        на них виден выигрыш гибридного поиска перед векторным
        """
        text = (
            f"{project.name} is a {project.type} project. "
            f"Current status is {project.status}."
        )
        chunks = [Chunk(text=text, order=0, mentions=(project,))]

        return Document(
            title=f"{project.name} overview",
            kind="project_overview",
            chunks=tuple(chunks),
        )

    @staticmethod
    def _person_profile(person: Person) -> Document:
        """Строит документ с профилем одного сотрудника

        Компания в тексте не называется - факт WORKS_AT есть только
        в связях графа. Навыки наоборот проговариваются, это дает
        векторному поиску зацепку для поиска людей по технологии
        """
        text = (
            f"{person.name} is a {person.position} with "
            f"{person.experience} years of experience."
        )
        chunks = [Chunk(text=text, order=0, mentions=(person,))]

        if person.skills:
            skill_names = ", ".join(t.name for t in person.skills)
            chunks.append(
                Chunk(
                    text=(
                        f"{person.name} has hands on experience "
                        f"with {skill_names}."
                    ),
                    order=len(chunks),
                    mentions=(person, *person.skills),
                )
            )

        return Document(
            title=f"{person.name} profile",
            kind="person_profile",
            chunks=tuple(chunks),
        )
