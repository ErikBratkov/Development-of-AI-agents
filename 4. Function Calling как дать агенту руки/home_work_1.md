# Домашнее задание к уроку №4 "Function Calling: как дать агенту руки"

Упражнение 1 (дизайн)

## Цель:

Спроектируйте инструмент `search_tickets` для Jira-базы РогаИКопыта:

- имя
- описание «когда использовать»
- полная JSON Schema
- аргументов (включая фильтры по статусу как enum, лимит результатов)
- формат возврата (ручки vs полные данные)
- три класса ошибок

Обоснуйте каждое решение.

# Решение упражнения 1 (дизайн): инструмент `search_tickets`

Проектируем read-only инструмент для поиска задач в Jira-базе компании РогаИКопыта.
Дизайн опирается на принципы из конспекта урока: строгая схема снижает число
галлюцинаций, контекст модели нельзя забивать большими данными (отдаем ручки),
ошибки возвращаем в структурированном виде как дополнительный контекст для LLM.

## 1. Имя

```
search_tickets
```

Обоснование. Имя в стиле "глагол + объект", читается без расшифровки. Слово
`search` сразу сообщает модели, что это поиск по фильтрам, а не выгрузка всего
подряд (`get_tickets`) и не работа с одной задачей (`get_ticket`). Никаких
абстракций вроде `do_query` или `tickets_api` - как и советует конспект, имя
должно быть понятным.

## 2. Описание «когда использовать»

> Находит задачи (тикеты) в Jira по набору фильтров: текст, проект, статус,
> исполнитель, приоритет, метки, диапазон дат. Возвращает короткий список
> карточек-ручек (ключ, заголовок, статус, исполнитель, приоритет, дата
> обновления) и курсор для следующей страницы. Используй, когда нужно найти
> подходящие задачи или проверить их наличие. НЕ используй для чтения полного
> описания и комментариев одной задачи - для этого есть `get_ticket(key)`. НЕ
> используй для создания или изменения задач - инструмент только читает.

Обоснование. Описание - это инструкция для модели, когда инструмент уместен, а
когда нет. Явный запрет ("не используй для...") отсекает типовые ошибки выбора
инструмента: попытку прочитать тело задачи через поиск или ожидание мутации от
read-only вызова. Сразу заявлен формат возврата (ручки), чтобы модель понимала,
что детали придется дозапрашивать.

## 3. Полная JSON Schema аргументов

```json
{
  "name": "search_tickets",
  "description": "Находит задачи в Jira по фильтрам и возвращает список ручек с курсором пагинации. Только чтение. Для полного содержимого задачи используй get_ticket(key).",
  "input_schema": {
    "type": "object",
    "properties": {
      "query": {
        "type": "string",
        "description": "Полнотекстовый поиск по заголовку и описанию. Можно опустить, если фильтрация только по полям",
        "maxLength": 200
      },
      "project": {
        "type": "string",
        "description": "Ключ проекта, например RK или OPS",
        "pattern": "^[A-Z][A-Z0-9]{1,9}$"
      },
      "status": {
        "type": "array",
        "description": "Фильтр по статусам. Пустой массив или отсутствие поля - любой статус",
        "items": {
          "type": "string",
          "enum": [
            "open",
            "in_progress",
            "in_review",
            "blocked",
            "done",
            "closed"
          ]
        },
        "uniqueItems": true
      },
      "assignee": {
        "type": "string",
        "description": "Логин исполнителя. Спецзначения: me - текущий пользователь, unassigned - без исполнителя"
      },
      "priority": {
        "type": "string",
        "enum": [
          "lowest",
          "low",
          "medium",
          "high",
          "highest"
        ],
        "description": "Фильтр по приоритету"
      },
      "labels": {
        "type": "array",
        "description": "Задача должна содержать все указанные метки",
        "items": {
          "type": "string"
        },
        "uniqueItems": true
      },
      "created_after": {
        "type": "string",
        "format": "date",
        "description": "Создано не раньше этой даты, формат YYYY-MM-DD"
      },
      "updated_after": {
        "type": "string",
        "format": "date",
        "description": "Обновлено не раньше этой даты, формат YYYY-MM-DD"
      },
      "sort": {
        "type": "string",
        "enum": [
          "created_desc",
          "created_asc",
          "updated_desc",
          "priority_desc"
        ],
        "default": "updated_desc",
        "description": "Порядок сортировки результатов"
      },
      "limit": {
        "type": "integer",
        "minimum": 1,
        "maximum": 50,
        "default": 20,
        "description": "Сколько задач вернуть за один вызов"
      },
      "cursor": {
        "type": "string",
        "description": "Курсор следующей страницы из поля next_cursor предыдущего ответа"
      }
    },
    "required": [],
    "additionalProperties": false
  }
}
```

Обоснование по ключевым полям.

1. `status` сделан массивом enum, а не свободной строкой. Это прямо требует
   задание и это главный приём против галлюцинаций - модель физически не может
   придумать несуществующий статус, а массив покрывает частый запрос вида "найди
   открытые и заблокированные". `uniqueItems` убирает дубли.
2. `limit` ограничен сверху значением 50 при умолчании 20. Это защита контекста
   из конспекта - нельзя позволить модели вытащить тысячи задач одним вызовом.
   Жёсткий потолок на стороне Runtime, даже если модель попросит больше.
3. `cursor` плюс `next_cursor` в ответе дают курсорную пагинацию. Модель сама
   решает, нужна ли следующая страница, и забирает её отдельным вызовом.
4. `project` ограничен регуляркой, `priority` и `sort` - тоже enum, у `sort` есть
   `default`. Чем строже схема, тем меньше места для выдуманных значений.
5. `required: []` - все аргументы необязательны, осмысленный поиск возможен по
   любому одному фильтру. `additionalProperties: false` запрещает левые поля:
   если модель выдумает аргумент, мы поймаем это валидацией и вернём понятную
   ошибку, а не молча проигнорируем.

## 4. Формат возврата: ручки против полных данных

Возвращаем именно ручки (handles), а не полные тела задач.

```json
{
  "total_count": 137,
  "returned": 20,
  "next_cursor": "eyJvZmZzZXQiOjIwfQ==",
  "tickets": [
    {
      "key": "RK-482",
      "summary": "Падает экспорт накладных в PDF",
      "status": "in_progress",
      "assignee": "i.petrov",
      "priority": "high",
      "updated_at": "2026-06-14"
    }
  ]
}
```

Обоснование. Конспект отдельно предупреждает: нельзя скармливать модели гигантские
выгрузки. Поэтому в карточке только минимум для принятия решения - ключ,
заголовок, статус, исполнитель, приоритет, дата обновления. Полное описание,
комментарии и история не возвращаются. Если модели нужны детали конкретной
задачи, она берёт `key` и вызывает `get_ticket(key)`. Поле `total_count`
показывает реальный размер выборки (например, нашлось 137, отдали 20), чтобы
модель честно сообщила пользователю масштаб и при необходимости пошла за
следующей страницей через `next_cursor`. Так контекст остаётся компактным, а
данные доступны по запросу.

## 5. Три класса ошибок

Ошибки возвращаем структурно через `tool_result` с `is_error: true` - для модели
это дополнительный контекст, по которому она может исправиться или сообщить
пользователю.

### Класс 1. Ошибка валидации (перманентная, чинит модель)

Например, передан несуществующий статус, `limit` больше 50, кривой формат даты
или лишнее поле. Повтор без изменений бесполезен.

```json
{
  "error_code": "INVALID_ARGUMENT",
  "message": "Поле status содержит недопустимое значение 'wip'. Разрешены: open, in_progress, in_review, blocked, done, closed",
  "retriable": false
}
```

Возвращаем модели, чтобы та поправила аргументы и вызвала инструмент заново.
Сообщение содержит список допустимых значений - это подсказка для самокоррекции.

### Класс 2. Транзиентная ошибка (временная, ретраит Runtime)

Jira недоступна, таймаут сети, ответ 5xx или 429. Runtime сам делает несколько
повторов с экспоненциальной задержкой. Если не помогло, отдаём модели.

```json
{
  "error_code": "TRANSIENT_UNAVAILABLE",
  "message": "Сервис Jira временно недоступен, повтор не помог. Попробуйте позже",
  "retriable": true
}
```

Обоснование. Конспект разделяет транзиентные и перманентные сбои: первые ретраим
с backoff на стороне Runtime, и только при стойком отказе сообщаем модели, чтобы
она предложила повторить позже.

### Класс 3. Ошибка прав доступа (перманентная, сообщает пользователю)

У текущего пользователя нет доступа к проекту или задачам. Повтор не поможет,
менять аргументы тоже бессмысленно.

```json
{
  "error_code": "PERMISSION_DENIED",
  "message": "Нет доступа к проекту OPS. Обратитесь к администратору Jira",
  "retriable": false
}
```

Обоснование. Это вопрос прав, а не аргументов. Модель не должна перебирать
варианты или зацикливаться на ретраях - её задача честно передать причину
пользователю. Важно: результаты по проектам без доступа вообще не подмешиваются в
выдачу, чтобы инструмент не стал каналом утечки.

Отдельно отмечу: пустой результат поиска - это НЕ ошибка. Возвращаем
`total_count: 0` и пустой массив `tickets`, иначе модель решит, что произошёл сбой,
и начнёт зря повторять запрос.

## Итог

Инструмент read-only, поэтому по политике безопасности из конспекта он может иметь
мягкую политику доступа (поиск ничего не меняет). Строгая схема с enum и потолком
`limit` гасит галлюцинации, формат ручек бережёт контекст, а три класса ошибок
дают модели понятный сценарий поведения: чинить аргументы, ждать после ретраев или
сообщать о нехватке прав.

## 6. Python-определение инструмента (стиль урока)

Ниже определение `search_tickets` в формате Messages API, как в примерах урока,
плюс Runtime-обвязка: валидация аргументов, ретраи транзиентных сбоев и упаковка
трёх классов ошибок в структурированный `tool_result`.

```python
"""Определение инструмента search_tickets в стиле урока 4

Инструмент только читает Jira и возвращает легкие ручки. Рядом показан
Runtime - валидация аргументов, ретраи транзиентных сбоев и упаковка ошибок
в структурированный tool_result для модели
"""

import json
import time
from typing import Any

# держим допустимые значения в константах чтобы схема и валидация
# не разъезжались между собой
TICKET_STATUSES = [
    "open",
    "in_progress",
    "in_review",
    "blocked",
    "done",
    "closed",
]

TICKET_PRIORITIES = ["lowest", "low", "medium", "high", "highest"]

SORT_ORDERS = ["created_desc", "created_asc", "updated_desc", "priority_desc"]

ALLOWED_FIELDS = {
    "query",
    "project",
    "status",
    "assignee",
    "priority",
    "labels",
    "created_after",
    "updated_after",
    "sort",
    "limit",
    "cursor",
}

MAX_LIMIT = 50
DEFAULT_LIMIT = 20

# определение инструмента в формате, который ждет Messages API
TOOLS = [
    {
        "name": "search_tickets",
        "description": (
            "Находит задачи в Jira по фильтрам и возвращает список ручек "
            "с курсором пагинации. Только чтение. Для полного содержимого "
            "задачи используй get_ticket(key)"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "maxLength": 200,
                    "description": (
                        "Полнотекстовый поиск по заголовку и описанию"
                    ),
                },
                "project": {
                    "type": "string",
                    "pattern": "^[A-Z][A-Z0-9]{1,9}$",
                    "description": "Ключ проекта, например RK или OPS",
                },
                "status": {
                    "type": "array",
                    "items": {"type": "string", "enum": TICKET_STATUSES},
                    "uniqueItems": True,
                    "description": "Фильтр по статусам, пусто - любой",
                },
                "assignee": {
                    "type": "string",
                    "description": (
                        "Логин исполнителя, спецзначения me и unassigned"
                    ),
                },
                "priority": {
                    "type": "string",
                    "enum": TICKET_PRIORITIES,
                    "description": "Фильтр по приоритету",
                },
                "labels": {
                    "type": "array",
                    "items": {"type": "string"},
                    "uniqueItems": True,
                    "description": "Задача должна содержать все метки",
                },
                "created_after": {
                    "type": "string",
                    "format": "date",
                    "description": "Создано не раньше даты YYYY-MM-DD",
                },
                "updated_after": {
                    "type": "string",
                    "format": "date",
                    "description": "Обновлено не раньше даты YYYY-MM-DD",
                },
                "sort": {
                    "type": "string",
                    "enum": SORT_ORDERS,
                    "default": "updated_desc",
                    "description": "Порядок сортировки",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_LIMIT,
                    "default": DEFAULT_LIMIT,
                    "description": "Сколько задач вернуть за вызов",
                },
                "cursor": {
                    "type": "string",
                    "description": "Курсор следующей страницы",
                },
            },
            "required": [],
            "additionalProperties": False,
        },
    }
]


class ToolError(Exception):
    """Базовая ошибка инструмента с кодом и признаком повтора"""

    def __init__(self, code: str, message: str, retriable: bool) -> None:
        """Сохраняет код, текст и признак возможности повтора"""
        super().__init__(message)
        self.code = code
        self.message = message
        self.retriable = retriable


class ValidationError(ToolError):
    """Перманентная ошибка валидации аргументов, чинит модель"""

    def __init__(self, message: str) -> None:
        """Создает ошибку валидации без права на повтор"""
        super().__init__("INVALID_ARGUMENT", message, False)


class TransientError(ToolError):
    """Временный сбой Jira, такое имеет смысл ретраить"""

    def __init__(self, message: str) -> None:
        """Создает транзиентную ошибку с разрешенным повтором"""
        super().__init__("TRANSIENT_UNAVAILABLE", message, True)


class AccessDeniedError(ToolError):
    """Нет прав на проект, повтор и смена аргументов не спасут"""

    def __init__(self, message: str) -> None:
        """Создает ошибку доступа без права на повтор"""
        super().__init__("PERMISSION_DENIED", message, False)


def validate_arguments(raw: dict[str, Any]) -> dict[str, Any]:
    """Проверяет аргументы и возвращает новый словарь с умолчаниями

    Не меняет переданный словарь. При нарушении схемы бросает
    ValidationError с понятным для модели текстом
    """
    extra = set(raw) - ALLOWED_FIELDS
    if extra:
        raise ValidationError(
            "Неизвестные поля: " + ", ".join(sorted(extra))
        )

    bad = [s for s in raw.get("status", []) if s not in TICKET_STATUSES]
    if bad:
        raise ValidationError(
            "Недопустимый статус: " + ", ".join(bad)
            + ". Разрешены: " + ", ".join(TICKET_STATUSES)
        )

    priority = raw.get("priority")
    if priority is not None and priority not in TICKET_PRIORITIES:
        raise ValidationError(
            "Недопустимый приоритет: " + str(priority)
            + ". Разрешены: " + ", ".join(TICKET_PRIORITIES)
        )

    sort = raw.get("sort")
    if sort is not None and sort not in SORT_ORDERS:
        raise ValidationError(
            "Недопустимый порядок сортировки: " + str(sort)
            + ". Разрешены: " + ", ".join(SORT_ORDERS)
        )

    limit = raw.get("limit", DEFAULT_LIMIT)
    if not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
        raise ValidationError(
            f"limit должен быть целым от 1 до {MAX_LIMIT}"
        )

    # собираем новый словарь, вход не трогаем
    clean = dict(raw)
    clean["limit"] = limit
    clean.setdefault("sort", "updated_desc")
    return clean


def _call_jira(args: dict[str, Any]) -> dict[str, Any]:
    """Заглушка обращения к Jira, в бою тут был бы REST вызов

    Возвращает уже урезанные ручки с учетом прав пользователя. В заглушке
    сбои не воспроизводятся, но реальный клиент может бросить TransientError
    при сбое сети и AccessDeniedError при отказе в доступе
    """
    # тут реальный клиент сходил бы в Jira и собрал только нужные поля
    return {
        "total_count": 1,
        "returned": 1,
        "next_cursor": None,
        "tickets": [
            {
                "key": "RK-482",
                "summary": "Падает экспорт накладных в PDF",
                "status": "in_progress",
                "assignee": "i.petrov",
                "priority": "high",
                "updated_at": "2026-06-14",
            }
        ],
    }


def search_tickets(
        args: dict[str, Any],
        max_retries: int = 3,
        base_delay: float = 0.5,
) -> dict[str, Any]:
    """Валидирует аргументы и ищет задачи с ретраями транзиентных сбоев

    Возвращает компактный ответ с ручками и курсором. Транзиентные сбои
    повторяет с экспоненциальной задержкой, перманентные пробрасывает сразу
    """
    clean = validate_arguments(args)

    attempt = 0
    while True:
        try:
            return _call_jira(clean)
        except TransientError:
            attempt += 1
            if attempt >= max_retries:
                raise
            # экспоненциальный backoff перед следущей попыткой
            time.sleep(base_delay * 2 ** (attempt - 1))


def _tool_result(
        tool_use_id: str,
        payload: dict[str, Any],
        is_error: bool = False,
) -> dict[str, Any]:
    """Собирает блок tool_result для отправки обратно модели"""
    block = {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": json.dumps(payload, ensure_ascii=False),
    }
    if is_error:
        block["is_error"] = True
    return block


def run_tool(
        name: str,
        tool_input: dict[str, Any],
        tool_use_id: str,
) -> dict[str, Any]:
    """Выполняет инструмент и пакует результат в блок tool_result

    Любую ToolError превращает в структурированный ответ с is_error чтобы
    модель могла исправиться, подождать или сообщить про нехватку прав
    """
    if name != "search_tickets":
        return _tool_result(
            tool_use_id,
            {
                "error_code": "INVALID_ARGUMENT",
                "message": f"Неизвестный инструмент: {name}",
                "retriable": False,
            },
            is_error=True,
        )

    try:
        return _tool_result(tool_use_id, search_tickets(tool_input))
    except ToolError as err:
        return _tool_result(
            tool_use_id,
            {
                "error_code": err.code,
                "message": err.message,
                "retriable": err.retriable,
            },
            is_error=True,
        )
```

Что показывает код:

1. Константы (`TICKET_STATUSES`, `TICKET_PRIORITIES`, `SORT_ORDERS`, `MAX_LIMIT`)
   переиспользуются и в схеме, и в валидации, поэтому enum в схеме и проверки в
   Runtime не разъезжаются.
2. `validate_arguments` чистая - не меняет вход, а возвращает новый словарь с
   подставленными умолчаниями. Это перехватывает выдуманные поля, статусы,
   приоритеты и порядок сортировки до похода в Jira. Формат дат, паттерн `project`
   и `maxLength` для `query` остаются на валидации уровня JSON Schema в API.
3. `search_tickets` ретраит только `TransientError` с экспоненциальной задержкой,
   а перманентные ошибки (валидация, доступ) пробрасывает сразу - ровно три класса
   из раздела 5.
4. `run_tool` любую ошибку отдаёт модели структурно через `is_error`, а не роняет
   агентный цикл, как в примере 4 из урока.
