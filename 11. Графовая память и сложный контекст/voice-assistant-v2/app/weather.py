import asyncio
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# публичные endpoints Open-Meteo, ключ не нужен
# для каждого запроса список адресов - основной и запасные, живущие
# на других IP. Основной хост api.open-meteo.com у некоторых
# провайдеров недоступен (соединение молча дропается), а
# historical-forecast-api отдает тот же /v1/forecast с current
GEOCODING_URLS = [
    "https://geocoding-api.open-meteo.com/v1/search",
]
FORECAST_URLS = [
    "https://api.open-meteo.com/v1/forecast",
    "https://historical-forecast-api.open-meteo.com/v1/forecast",
]

REQUEST_TIMEOUT = 10.0
# отдельный короткий таймаут на установку соединения, чтобы быстро
# переключаться на запасной адрес когда основной не отвечает
CONNECT_TIMEOUT = 5.0
RETRIES = 3
RETRY_BASE_DELAY = 1.0

# описание инструмента в формате OpenAI function calling,
# именно по нему модель понимает когда и как звать функцию
WEATHER_TOOL_SPEC: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": (
            "Возвращает текущую погоду в указанном городе по данным "
            "Open-Meteo. Используй всегда, когда пользователь "
            "спрашивает про погоду, температуру, ветер или осадки "
            "в каком-либо городе или месте."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": (
                        "Название города на любом языке, "
                        "например 'Москва' или 'Paris'"
                    ),
                },
            },
            "required": ["city"],
        },
    },
}

# расшифровка кодов погоды WMO из ответа Open-Meteo
_WEATHER_CODES: dict[int, str] = {
    0: "ясно",
    1: "преимущественно ясно",
    2: "переменная облачность",
    3: "пасмурно",
    45: "туман",
    48: "изморозь",
    51: "легкая морось",
    53: "морось",
    55: "сильная морось",
    56: "переохлажденная морось",
    57: "сильная переохлажденная морось",
    61: "небольшой дождь",
    63: "дождь",
    65: "сильный дождь",
    66: "ледяной дождь",
    67: "сильный ледяной дождь",
    71: "небольшой снег",
    73: "снег",
    75: "сильный снегопад",
    77: "снежная крупа",
    80: "небольшой ливень",
    81: "ливень",
    82: "очень сильный ливень",
    85: "небольшой снегопад",
    86: "снегопад",
    95: "гроза",
    96: "гроза с небольшим градом",
    99: "гроза с сильным градом",
}


def describe_weather_code(code: int | None) -> str:
    """Переводит WMO код погоды в короткое описание на русском"""
    if code is None:
        return "нет данных об осадках"
    return _WEATHER_CODES.get(code, "неизвестные погодные условия")


def format_weather_report(
    place: dict[str, Any], current: dict[str, Any]
) -> str:
    """Собирает текстовую сводку погоды из ответов Open-Meteo

    Функция чистая, аргументы не меняет. Текст отдается модели как
    результат инструмента, она перескажет его пользователю
    """
    name = place.get("name", "")
    country = place.get("country", "")
    location = name if not country else name + ", " + country
    parts = [
        "Текущая погода в месте: " + location,
        "условия: " + describe_weather_code(current.get("weather_code")),
    ]
    temperature = current.get("temperature_2m")
    if temperature is not None:
        parts.append(f"температура {temperature} градусов Цельсия")
    feels_like = current.get("apparent_temperature")
    if feels_like is not None:
        parts.append(f"ощущается как {feels_like}")
    humidity = current.get("relative_humidity_2m")
    if humidity is not None:
        parts.append(f"влажность {humidity} процентов")
    wind = current.get("wind_speed_10m")
    if wind is not None:
        parts.append(f"ветер {wind} м/с")
    return ", ".join(parts)


async def _get_json(
    client: httpx.AsyncClient, urls: list[str], params: dict[str, Any]
) -> dict[str, Any]:
    """GET запрос с повторами, возвращает json первого удачного ответа

    В каждой попытке перебираем все адреса по очереди - если основной
    хост недоступен (например срезается провайдером), сразу идем на
    запасной, не дожидаясь исчерпания повторов. Повторяем только то,
    что имеет смысл повторять - сетевые сбои, таймауты и 5xx/429.
    Ошибки 4xx отдаем наружу сразу
    """
    last_error: Exception | None = None
    for attempt in range(RETRIES):
        for url in urls:
            try:
                response = await client.get(url, params=params)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status != 429 and status < 500:
                    raise
                last_error = exc
            except httpx.HTTPError as exc:
                last_error = exc
            logger.warning(
                "ошибка запроса к %s, попытка %d из %d: %s",
                url,
                attempt + 1,
                RETRIES,
                str(last_error) or last_error.__class__.__name__,
            )
        if attempt < RETRIES - 1:
            delay = RETRY_BASE_DELAY * 2 ** attempt
            await asyncio.sleep(delay)
    raise httpx.HTTPError(
        f"Open-Meteo не ответил после {RETRIES} попыток"
    ) from last_error


async def get_weather(city: str) -> str:
    """Ходит в Open-Meteo и возвращает сводку текущей погоды текстом

    Сначала геокодинг названия города в координаты, затем запрос
    текущей погоды. Исключения наружу не выпускаем - модель должна
    получить понятный текст и пересказать его пользователю
    """
    timeout = httpx.Timeout(REQUEST_TIMEOUT, connect=CONNECT_TIMEOUT)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            geo = await _get_json(
                client,
                GEOCODING_URLS,
                {"name": city, "count": 1, "language": "ru", "format": "json"},
            )
            results = geo.get("results") or []
            if not results:
                return (
                    f"Город '{city}' не найден в справочнике Open-Meteo, "
                    "уточни название у пользователя"
                )
            place = results[0]
            forecast = await _get_json(
                client,
                FORECAST_URLS,
                {
                    "latitude": place["latitude"],
                    "longitude": place["longitude"],
                    "current": (
                        "temperature_2m,apparent_temperature,"
                        "relative_humidity_2m,wind_speed_10m,weather_code"
                    ),
                    "wind_speed_unit": "ms",
                    "timezone": "auto",
                },
            )
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        logger.error("не удалось получить погоду для '%s': %s", city, exc)
        return "Сервис погоды сейчас недоступен, попробуйте позже"
    current = forecast.get("current") or {}
    return format_weather_report(place, current)
