"""Описание предметной области.

Данные делятся на два вида. Справочники (технологии, компании,
должности) переносятся в граф как есть, ровно по одному разу.
Экземпляры (люди, проекты) генерируются случайно поверх справочников.
"""

SEED = 42

# справочник технологий, зависимости указываются по имени
# и должны присутствовать в этом же списке
TECHNOLOGIES = [
    {"name": "Python", "category": "backend", "depends_on": []},
    {"name": "FastAPI", "category": "backend", "depends_on": ["Python"]},
    {"name": "Django", "category": "backend", "depends_on": ["Python"]},
    {
        "name": "Celery",
        "category": "backend",
        "depends_on": ["Python", "RabbitMQ"],
    },
    {"name": "PostgreSQL", "category": "database", "depends_on": []},
    {"name": "Neo4j", "category": "database", "depends_on": []},
    {"name": "Redis", "category": "database", "depends_on": []},
    {"name": "Kafka", "category": "messaging", "depends_on": []},
    {"name": "RabbitMQ", "category": "messaging", "depends_on": []},
    {"name": "Docker", "category": "infrastructure", "depends_on": []},
    {
        "name": "Kubernetes",
        "category": "infrastructure",
        "depends_on": ["Docker"],
    },
    {"name": "Grafana", "category": "infrastructure", "depends_on": []},
]

COMPANIES = [
    "Acme",
    "Globex",
    "Umbrella",
    "Initech",
    "Contoso",
    "Cyberdyne",
]

POSITIONS = [
    "Backend Developer",
    "Frontend Developer",
    "QA Engineer",
    "DevOps Engineer",
    "Architect",
    "Team Lead",
]

# имен должно хватать на PROJECT_COUNT проектов без повторов
PROJECT_NAMES = [
    "CRM",
    "Payments",
    "Analytics",
    "Gateway",
    "Identity",
    "Notification",
    "Reporting",
    "Billing",
    "Inventory",
    "Search",
    "Catalog",
    "Delivery",
    "Scoring",
    "Monitoring",
    "Archive",
    "Helpdesk",
]

PROJECT_STATUSES = ["NEW", "ACTIVE", "DONE"]

# какие категории технологий допустимы в стеке проекта каждого типа
PROJECT_TYPE_CATEGORIES = {
    "backend": ("backend", "database"),
    "integration": ("messaging", "backend"),
    "analytics": ("database", "backend"),
}

PERSON_COUNT = 50
PROJECT_COUNT = 15

EXPERIENCE_RANGE = (1, 20)
SKILLS_PER_PERSON = (1, 5)
TEAM_SIZE = (2, 6)
STACK_SIZE = (2, 4)
