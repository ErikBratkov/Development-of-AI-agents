from app.weather import describe_weather_code, format_weather_report


def test_describe_weather_code_known() -> None:
    """Известный WMO код переводится в короткое описание"""
    assert describe_weather_code(0) == "ясно"
    assert describe_weather_code(95) == "гроза"


def test_describe_weather_code_unknown_and_none() -> None:
    """Неизвестный код и отсутствие кода не роняют функцию"""
    assert describe_weather_code(12345) == "неизвестные погодные условия"
    assert describe_weather_code(None) == "нет данных об осадках"


def test_format_weather_report_full() -> None:
    """Сводка собирает все доступные поля"""
    place = {"name": "Москва", "country": "Россия"}
    current = {
        "weather_code": 3,
        "temperature_2m": -5.2,
        "apparent_temperature": -9.8,
        "relative_humidity_2m": 87,
        "wind_speed_10m": 4.1,
    }
    report = format_weather_report(place, current)
    assert "Москва, Россия" in report
    assert "пасмурно" in report
    assert "температура -5.2" in report
    assert "ощущается как -9.8" in report
    assert "влажность 87" in report
    assert "ветер 4.1 м/с" in report


def test_format_weather_report_partial_data() -> None:
    """Отсутствующие поля просто пропускаются"""
    report = format_weather_report({"name": "Тула"}, {})
    assert "Тула" in report
    assert "температура" not in report
    assert "нет данных об осадках" in report


def test_format_weather_report_does_not_mutate_args() -> None:
    """Функция чистая - входные словари не меняются"""
    place = {"name": "Казань", "country": "Россия"}
    current = {"temperature_2m": 20}
    place_copy = dict(place)
    current_copy = dict(current)
    format_weather_report(place, current)
    assert place == place_copy
    assert current == current_copy
