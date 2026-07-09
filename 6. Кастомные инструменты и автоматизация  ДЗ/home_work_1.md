# Домашнее задание к уроку №6 "Кастомные инструменты и автоматизация // ДЗ"

Упражнение 1

## Цель:

Реализовать вызов внешнего инструмента и оформить логику его использования в виде SOP;

## Описание/Пошаговая инструкция выполнения домашнего задания:

- Определите одну функцию (API или SQL);
- Опишите JSON-схему функции;
- Реализуйте вызов функции через LLM;
- Оформите SOP для использования инструмента;
- Добавьте обработку базовой ошибки;

## Формат сдачи:

- код + JSON-схема + SOP (1 файл)

## Критерии оценки:

Задание считается принятым, если:

- реализован вызов инструмента;
- схема функции валидна;
- описан SOP использования;

## Компетенции:

- Интегрировать агента с рабочими системами
- настраивать вызов внешних функций через LLM.
- интегрировать агента с API и сервисами.

---

## Решение

Выбран один кастомный SQL-инструмент `get_inventory_balance` - проверка
складского остатка по артикулу. Инструмент read-only, поэтому политика
доступа = allow (mutating-операций нет, idempotency не требуется).

По разбору с вебинара tool состоит из двух слоев:
- контракт для LLM (name, description, строгая JSON Schema)
- исполнение в Runtime (валидация до вызова, параметризованный SQL,
  компактный результат OK/ERROR, классификация ошибок и ретраи)

Граница ответственности: LLM решает только когда и с какими аргументами
позвать tool, а вся детерминированная логика (валидация, SQL, обработка
ошибок) находится в коде.

Рядом с этим документом лежит запускаемый файл [home_work_1.py](home_work_1.py)
с тем же кодом - можно выполнить `python home_work_1.py` без копирования
из fenced block.

### JSON-схема инструмента

```json
{
  "name": "get_inventory_balance",
  "description": "Возвращает складской остаток по артикулу (SKU). Использовать, когда пользователь спрашивает наличие, остаток или сколько товара доступно к заказу. Не использовать для изменения остатков и для резервирования товара - инструмент работает только на чтение.",
  "parameters": {
    "type": "object",
    "additionalProperties": false,
    "required": ["sku"],
    "properties": {
      "sku": {
        "type": "string",
        "description": "Артикул товара, например SKU-100"
      },
      "warehouse": {
        "type": "string",
        "description": "Код склада. Если пользователь не указал склад - передай any (сумма по всем складам)",
        "enum": ["msk", "spb", "ekb", "any"]
      }
    }
  }
}
```

`additionalProperties: false` запрещает лишние поля, `enum` закрывает список
складов - это снижает шанс галлюцинаций модели.

Ключевое слово `default` в схеме сознательно не используется: провайдеры
не подставляют его в arguments сами, а strict mode у части провайдеров может
об него споткнуться. Поэтому подсказка для модели живет в description, а
само значение по умолчанию подставляет Runtime после получения аргументов
(`args.get("warehouse", "any")`) - так поведение одинаково у Anthropic,
в OpenRouter и в офлайн-режиме.

### Обёртки схемы под провайдеров

Выше показан нейтральный вид схемы, но в запросе к конкретному API она
оборачивается по-разному. Нативный формат Anthropic (схема лежит в
`input_schema`):

```json
{
  "name": "get_inventory_balance",
  "description": "...",
  "input_schema": { "type": "object", "...": "..." }
}
```

OpenAI-совместимый формат (OpenRouter): обёртка `type: function`, схема
лежит в `parameters`:

```json
{
  "type": "function",
  "function": {
    "name": "get_inventory_balance",
    "description": "...",
    "parameters": { "type": "object", "...": "..." }
  }
}
```

В коде обе обёртки собираются из одной нейтральной `TOOL_SCHEMA` функциями
`_anthropic_tool()` и `_openrouter_tool()`.

### SOP использования инструмента

```text
SOP: get_inventory_balance

1. Когда использовать: пользователь спрашивает наличие или остаток товара по артикулу
2. Когда НЕ использовать: запросы на списание, резерв или изменение остатка
3. Обязательный вход: sku. Необязательный: warehouse (если не передан - runtime подставит any)
4. Валидация: проверить что sku - непустая строка, warehouse из списка enum
5. Вызов: только параметризованный SQL, пользовательский ввод не склеивать со строкой запроса
6. Если status OK - сообщить свободный остаток (free) и предложить следующий шаг
7. Если status NOT_FOUND - попросить пользователя уточнить артикул
8. На validation_error - запросить корректный ввод, инструмент не повторять
9. На transient (timeout, блокировка БД) - инструмент сам делает ретрай
   с backoff, при исчерпании ретраев эскалировать оператору
10. Безопасность: tool read-only, policy = allow, меняющих операций нет
```

### Обработка ошибок

Используются классы ошибок из конспекта:
- `validation_error` - нет required поля, лишнее поле, неверный enum. Модели
  возвращается ошибка, повтор не делается
- `NOT_FOUND` - смысловой результат, не сбой. Просим уточнить артикул
- `transient_error` - временный сбой БД (timeout, блокировка). Делается ретрай
  с backoff, безопасно только потому что инструмент read-only
- `tool_error` - последний рубеж для любой непредвиденной ошибки

### Полный цикл агента

Цикл не обрывается на JSON результата tool: после исполнения инструмента
делается второй вызов LLM, в который передается tool_result (блок
`tool_result` у Anthropic, сообщение `role=tool` у OpenRouter), и модель
формулирует финальный текстовый ответ пользователю. В офлайн-режиме и при
сбое второго вызова ответ собирается детерминированно из результата tool.

### Код (код + JSON-схема + SOP в одном файле)

```python
"""ДЗ №6: кастомный SQL-инструмент get_inventory_balance.

Один файл: JSON-схема (контракт для LLM) + код инструмента (Runtime) + SOP
+ базовая обработка ошибок. Идея с вебинара: LLM только решает когда и с
какими аргументами вызвать tool, а вся детерминированная логика (валидация,
параметризованный SQL, классификация ошибок, ретраи) живет в коде.

Полный цикл агента: первый вызов LLM выбирает tool и аргументы, Runtime
исполняет tool, второй вызов LLM получает результат и формулирует финальный
ответ пользователю.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from typing import Any

# Контракт инструмента для модели. Схему держим строгой - так у модели
# меньше шансов нагаллюцинировать лишние поля.
# default в схеме сознательно не используем: провайдеры его не подставляют,
# а strict mode может об него споткнуться. Значение по умолчанию живет
# в runtime, а модели даем подсказку через description
TOOL_SCHEMA: dict[str, Any] = {
    "name": "get_inventory_balance",
    "description": (
        "Возвращает складской остаток по артикулу (SKU). "
        "Использовать, когда пользователь спрашивает наличие, остаток или "
        "сколько товара доступно к заказу. "
        "Не использовать для изменения остатков и для резервирования товара - "
        "инструмент работает только на чтение."
    ),
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "required": ["sku"],
        "properties": {
            "sku": {
                "type": "string",
                "description": "Артикул товара, например SKU-100",
            },
            "warehouse": {
                "type": "string",
                "description": (
                    "Код склада. Если пользователь не указал склад - "
                    "передай any (сумма по всем складам)"
                ),
                "enum": ["msk", "spb", "ekb", "any"],
            },
        },
    },
}

# SOP можно положить прямо в системный промпт как runtime-инструкцию
SOP = """
SOP: get_inventory_balance

1. Когда использовать: пользователь спрашивает наличие или остаток товара по артикулу
2. Когда НЕ использовать: запросы на списание, резерв или изменение остатка
3. Обязательный вход: sku. Необязательный: warehouse (если не передан - runtime подставит any)
4. Валидация: проверить что sku - непустая строка, warehouse из списка enum
5. Вызов: только параметризованный SQL, пользовательский ввод не склеивать со строкой запроса
6. Если status OK - сообщить свободный остаток (free) и предложить следующий шаг
7. Если status NOT_FOUND - попросить пользователя уточнить артикул
8. На validation_error - запросить корректный ввод, инструмент не повторять
9. На transient (timeout, блокировка БД) - инструмент сам делает ретрай
   с backoff, при исчерпании ретраев эскалировать оператору
10. Безопасность: tool read-only, policy = allow, меняющих операций нет
"""

# Имитация in-memory витрины остатков. В бою тут был бы пул соединений к реальной БД
INVENTORY = [
    ("SKU-100", "msk", 50, 12),
    ("SKU-100", "spb", 8, 0),
    ("SKU-200", "ekb", 3, 3),
    # запись для флаки-кейса - чтобы после ретрая был осмысленный остаток
    ("SKU-FLAKY", "msk", 10, 2),
]

# для какого sku разок роняем БД, потом запрос проходит - так видно что ретрай восстанавливается
_FLAKY_STATE: dict[str, int] = {}

MAX_RETRIES = 2
RETRY_BACKOFF_SEC = 0.05


def validate(schema: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    """Проверяет аргументы по JSON-схеме до вызова инструмента."""
    parameters = schema["parameters"]
    properties = parameters.get("properties", {})

    for field in parameters.get("required", []):
        if field not in args:
            return _err("validation_error", f"Missing required field: {field}")

    # лишние поля запрещаем явно, чтобы не пропустить опечатку модели
    extra = set(args.keys()) - set(properties.keys())
    if extra:
        return _err("validation_error", f"Unexpected fields: {sorted(extra)}")

    sku = args.get("sku")
    if not isinstance(sku, str) or not sku.strip():
        return _err("validation_error", "sku must be a non-empty string")

    warehouse = args.get("warehouse", "any")
    allowed = properties["warehouse"]["enum"]
    if warehouse not in allowed:
        return _err("validation_error", f"warehouse must be one of {allowed}")

    return {"status": "OK"}


def _err(error_type: str, message: str) -> dict[str, Any]:
    """Собирает компактный ответ об ошибке в едином формате."""
    return {"status": "ERROR", "error_type": error_type, "message": message}


def _query_balance(connection: sqlite3.Connection, sku: str, warehouse: str) -> list[sqlite3.Row]:
    """Делает параметризованный SELECT по остаткам, без склейки строк."""
    if warehouse == "any":
        sql = (
            "SELECT warehouse, available, reserved FROM inventory "
            "WHERE sku = ?"
        )
        params: tuple[Any, ...] = (sku,)
    else:
        sql = (
            "SELECT warehouse, available, reserved FROM inventory "
            "WHERE sku = ? AND warehouse = ?"
        )
        params = (sku, warehouse)
    return connection.execute(sql, params).fetchall()


def build_demo_db() -> sqlite3.Connection:
    """Поднимает временную БД с тестовой витриной остатков."""
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute(
        "CREATE TABLE inventory ("
        "sku TEXT, warehouse TEXT, available INTEGER, reserved INTEGER)"
    )
    connection.executemany(
        "INSERT INTO inventory VALUES (?, ?, ?, ?)",
        INVENTORY,
    )
    return connection


def _maybe_flaky(sku: str) -> None:
    """Один раз роняет БД на спец-артикуле, дальше запрос проходит штатно."""
    if sku != "SKU-FLAKY":
        return
    attempts = _FLAKY_STATE.get(sku, 0)
    if attempts < 1:
        _FLAKY_STATE[sku] = attempts + 1
        raise sqlite3.OperationalError("database is locked")


def get_inventory_balance(sku: str, warehouse: str = "any") -> dict[str, Any]:
    """Возвращает остаток по артикулу.

    Не модифицирует входные аргументы, но имеет побочный эффект - чтение из БД
    и обновление модульного _FLAKY_STATE при симуляции временного сбоя.
    """
    sku = sku.strip().upper()

    # ретраи только для временных сбоев - валидацию и not_found повторять нельзя
    for attempt in range(MAX_RETRIES + 1):
        try:
            _maybe_flaky(sku)
            with build_demo_db() as connection:
                rows = _query_balance(connection, sku, warehouse)
            break
        except sqlite3.OperationalError as exc:
            if attempt >= MAX_RETRIES:
                return _err("transient_error", f"DB unavailable after retries: {exc}")
            time.sleep(RETRY_BACKOFF_SEC * (attempt + 1))

    if not rows:
        # запись по sku может существовать, но не на запрошенном складе
        if warehouse == "any":
            message = "No inventory record for this SKU"
        else:
            message = "No inventory record for this SKU on this warehouse"
        return {
            "status": "NOT_FOUND",
            "sku": sku,
            "warehouse": warehouse,
            "message": message,
        }

    available = sum(row["available"] for row in rows)
    reserved = sum(row["reserved"] for row in rows)
    return {
        "status": "OK",
        "sku": sku,
        "warehouse": warehouse,
        "available": available,
        "reserved": reserved,
        "free": available - reserved,
    }


def fake_llm(user_request: str) -> dict[str, Any]:
    """Заглушка LLM для офлайн-прогона, если нет ключа или сети."""
    text = user_request.lower()
    sku = "SKU-100"
    for token in user_request.replace(",", " ").split():
        if token.upper().startswith("SKU-"):
            sku = token.upper()
            break
    warehouse = "any"
    for code in ("msk", "spb", "ekb"):
        if code in text:
            warehouse = code
            break
    return {
        "type": "tool_call",
        "provider": "offline",
        "tool": TOOL_SCHEMA["name"],
        "arguments": {"sku": sku, "warehouse": warehouse},
    }


# Дефолтные модели под каждого провайдера. Для OpenRouter имя через слэш,
# можно переопределить через переменную окружения OPENROUTER_MODEL
ANTHROPIC_MODEL = "claude-opus-4-8"
OPENROUTER_MODEL = "anthropic/claude-opus-4-8"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def _anthropic_tool() -> dict[str, Any]:
    """Оборачивает нейтральную схему в формат tools нативного API Anthropic."""
    return {
        "name": TOOL_SCHEMA["name"],
        "description": TOOL_SCHEMA["description"],
        "input_schema": TOOL_SCHEMA["parameters"],
    }


def _openrouter_tool() -> dict[str, Any]:
    """Оборачивает нейтральную схему в формат tools OpenAI (function calling)."""
    # у OpenAI-формата tool обернут в function, а схема лежит в parameters
    return {
        "type": "function",
        "function": {
            "name": TOOL_SCHEMA["name"],
            "description": TOOL_SCHEMA["description"],
            "parameters": TOOL_SCHEMA["parameters"],
        },
    }


def _select_provider() -> str:
    """Выбирает провайдера LLM по доступным ключам, иначе офлайн-режим."""
    # OpenRouter в приоритете - если задан его ключ, идем через него
    if os.environ.get("OPENROUTER_API_KEY"):
        return "openrouter"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    return "offline"


def _decide_via_anthropic(user_request: str) -> dict[str, Any]:
    """Запрашивает решение о вызове tool через нативный API Anthropic."""
    import anthropic

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=1024,
        system=SOP.strip(),
        tools=[_anthropic_tool()],
        messages=[{"role": "user", "content": user_request}],
    )
    for block in response.content:
        if block.type == "tool_use":
            # tool_use_id и content ассистента нужны для второго вызова с результатом
            return {
                "type": "tool_call",
                "provider": "anthropic",
                "tool": block.name,
                "arguments": dict(block.input),
                "tool_use_id": block.id,
                "assistant_content": response.content,
            }
    # модель решила не звать tool - отдаем текстовый ответ как есть
    return {"type": "text", "provider": "anthropic", "tool": None, "arguments": {}}


def _decide_via_openrouter(user_request: str) -> dict[str, Any]:
    """Запрашивает решение о вызове tool через OpenRouter (OpenAI-совместимый формат)."""
    import openai

    client = openai.OpenAI(
        base_url=OPENROUTER_BASE_URL,
        api_key=os.environ["OPENROUTER_API_KEY"],
    )
    response = client.chat.completions.create(
        model=os.environ.get("OPENROUTER_MODEL", OPENROUTER_MODEL),
        max_tokens=1024,
        messages=[
            {"role": "system", "content": SOP.strip()},
            {"role": "user", "content": user_request},
        ],
        tools=[_openrouter_tool()],
    )
    message = response.choices[0].message
    tool_calls = message.tool_calls or []
    for call in tool_calls:
        # arguments тут приходят строкой с JSON, в отличие от готового dict у Anthropic
        arguments = json.loads(call.function.arguments or "{}")
        return {
            "type": "tool_call",
            "provider": "openrouter",
            "tool": call.function.name,
            "arguments": arguments,
            "tool_call_id": call.id,
        }
    # модель решила не звать tool - отдаем текстовый ответ как есть
    return {"type": "text", "provider": "openrouter", "tool": None, "arguments": {}}


def decide_tool_call(user_request: str) -> dict[str, Any]:
    """Спрашивает у реальной LLM какой tool вызвать, при отказе - фолбэк на заглушку."""
    provider = _select_provider()
    if provider == "offline":
        return fake_llm(user_request)
    try:
        if provider == "openrouter":
            return _decide_via_openrouter(user_request)
        return _decide_via_anthropic(user_request)
    except Exception:
        # сеть, лимиты, кривой ключ - не валим агент, уходим в офлайн-режим
        return fake_llm(user_request)


def _finalize_via_anthropic(
    user_request: str, decision: dict[str, Any], tool_result: dict[str, Any]
) -> str:
    """Второй вызов Anthropic: отдает модели tool_result и получает финальный текст."""
    import anthropic

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=1024,
        system=SOP.strip(),
        tools=[_anthropic_tool()],
        messages=[
            {"role": "user", "content": user_request},
            {"role": "assistant", "content": decision["assistant_content"]},
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": decision["tool_use_id"],
                        "content": json.dumps(tool_result, ensure_ascii=False),
                    }
                ],
            },
        ],
    )
    parts = [block.text for block in response.content if block.type == "text"]
    return " ".join(parts).strip()


def _finalize_via_openrouter(
    user_request: str, decision: dict[str, Any], tool_result: dict[str, Any]
) -> str:
    """Второй вызов OpenRouter: сообщение role=tool с результатом и финальный текст."""
    import openai

    client = openai.OpenAI(
        base_url=OPENROUTER_BASE_URL,
        api_key=os.environ["OPENROUTER_API_KEY"],
    )
    assistant_message = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": decision["tool_call_id"],
                "type": "function",
                "function": {
                    "name": decision["tool"],
                    "arguments": json.dumps(decision["arguments"], ensure_ascii=False),
                },
            }
        ],
    }
    response = client.chat.completions.create(
        model=os.environ.get("OPENROUTER_MODEL", OPENROUTER_MODEL),
        max_tokens=1024,
        messages=[
            {"role": "system", "content": SOP.strip()},
            {"role": "user", "content": user_request},
            assistant_message,
            {
                "role": "tool",
                "tool_call_id": decision["tool_call_id"],
                "content": json.dumps(tool_result, ensure_ascii=False),
            },
        ],
        tools=[_openrouter_tool()],
    )
    return (response.choices[0].message.content or "").strip()


def offline_final_answer(tool_result: dict[str, Any]) -> str:
    """Собирает текстовый ответ пользователю без LLM - для офлайн-режима."""
    status = tool_result.get("status")
    if status == "OK":
        warehouse = tool_result["warehouse"]
        place = "по всем складам" if warehouse == "any" else f"на складе {warehouse}"
        return (
            f"По артикулу {tool_result['sku']} {place} свободно "
            f"{tool_result['free']} шт (доступно {tool_result['available']}, "
            f"в резерве {tool_result['reserved']})"
        )
    if status == "NOT_FOUND":
        return (
            f"Артикул {tool_result.get('sku')} не найден, "
            "уточните пожалуйста артикул"
        )
    return f"Не удалось получить остаток: {tool_result.get('message')}"


def compose_final_answer(
    user_request: str, decision: dict[str, Any], tool_result: dict[str, Any]
) -> str:
    """Второй шаг цикла: LLM превращает JSON результата tool в ответ пользователю."""
    provider = decision.get("provider", "offline")
    try:
        if provider == "anthropic":
            return _finalize_via_anthropic(user_request, decision, tool_result)
        if provider == "openrouter":
            return _finalize_via_openrouter(user_request, decision, tool_result)
    except Exception:
        # сбой второго вызова не критичен - соберем ответ детерминированно
        pass
    return offline_final_answer(tool_result)


def run_agent(user_request: str) -> dict[str, Any]:
    """Полный цикл: решение LLM -> валидация -> вызов tool -> второй вызов LLM
    с результатом -> финальный ответ пользователю."""
    decision = decide_tool_call(user_request)
    if decision.get("type") != "tool_call":
        return _err("no_tool_call", "LLM did not request a tool call")
    if decision.get("tool") != TOOL_SCHEMA["name"]:
        return _err("unknown_tool", f"Unknown tool: {decision.get('tool')}")

    args = decision.get("arguments", {})
    validation = validate(TOOL_SCHEMA, args)
    if validation["status"] != "OK":
        return validation

    # значение по умолчанию подставляет runtime, на default в схеме не полагаемся
    if "warehouse" not in args:
        args = {**args, "warehouse": "any"}

    try:
        result = get_inventory_balance(**args)
    except Exception as exc:
        # последний рубеж - любую непредвиденную ошибку показываем модели как tool_error
        return _err("tool_error", str(exc))

    final_answer = compose_final_answer(user_request, decision, result)
    return {
        "status": "OK",
        "tool_call": {
            "tool": decision["tool"],
            "arguments": args,
            "provider": decision.get("provider"),
        },
        "tool_result": result,
        "final_answer": final_answer,
    }


if __name__ == "__main__":
    checks = [
        "Сколько SKU-100 доступно на складе msk?",
        "Остаток по артикулу SKU-200",
        "Есть ли SKU-999 в наличии?",
        "Проверь наличие SKU-FLAKY",
    ]
    for request in checks:
        print(request)
        outcome = run_agent(request)
        # при ошибке выше по пайплайну tool_result не будет - печатаем весь ответ
        payload = outcome.get("tool_result", outcome)
        print(json.dumps(payload, ensure_ascii=False))
        print("Ответ:", outcome.get("final_answer", "-"))
        print("-" * 40)
```

### Пример работы

Запуск `python home_work_1.py` (файл лежит рядом с этим документом)
для офлайн-режима без ключа ANTHROPIC_API_KEY:

```text
Сколько SKU-100 доступно на складе msk?
{"status": "OK", "sku": "SKU-100", "warehouse": "msk", "available": 50, "reserved": 12, "free": 38}
Ответ: По артикулу SKU-100 на складе msk свободно 38 шт (доступно 50, в резерве 12)
----------------------------------------
Остаток по артикулу SKU-200
{"status": "OK", "sku": "SKU-200", "warehouse": "any", "available": 3, "reserved": 3, "free": 0}
Ответ: По артикулу SKU-200 по всем складам свободно 0 шт (доступно 3, в резерве 3)
----------------------------------------
Есть ли SKU-999 в наличии?
{"status": "NOT_FOUND", "sku": "SKU-999", "warehouse": "any", "message": "No inventory record for this SKU"}
Ответ: Артикул SKU-999 не найден, уточните пожалуйста артикул
----------------------------------------
Проверь наличие SKU-FLAKY
{"status": "OK", "sku": "SKU-FLAKY", "warehouse": "any", "available": 10, "reserved": 2, "free": 8}
Ответ: По артикулу SKU-FLAKY по всем складам свободно 8 шт (доступно 10, в резерве 2)
----------------------------------------
```

Кейс `SKU-FLAKY` показывает работу ретраев: первый запрос к БД падает с
временной ошибкой, инструмент сам делает повтор с backoff, и второй запрос
уже отдает остаток. Снаружи сбой не виден - получаем штатный status OK.

Если задан ключ ANTHROPIC_API_KEY или OPENROUTER_API_KEY, агент работает
в два вызова LLM: первый выбирает tool и аргументы (SOP передается в
system-промпт), второй получает tool_result и формулирует финальный ответ
пользователю. При сбое сети или лимитов агент безопасно откатывается на
офлайн-режим.
