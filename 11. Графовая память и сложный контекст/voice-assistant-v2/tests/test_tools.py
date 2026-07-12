from app.tools import TOOL_HANDLERS, TOOLS_SPEC


def test_specs_and_handlers_consistent() -> None:
    """Каждой спеке соответствует обработчик и наоборот"""
    spec_names = {spec["function"]["name"] for spec in TOOLS_SPEC}
    assert spec_names == set(TOOL_HANDLERS)


def test_expected_tools_registered() -> None:
    """В реестре оба инструмента - погода и база знаний"""
    assert "get_weather" in TOOL_HANDLERS
    assert "search_knowledge" in TOOL_HANDLERS


def test_specs_shape() -> None:
    """Спеки оформлены как function calling с параметрами-объектом"""
    for spec in TOOLS_SPEC:
        assert spec["type"] == "function"
        assert spec["function"]["parameters"]["type"] == "object"
        assert spec["function"]["description"]
