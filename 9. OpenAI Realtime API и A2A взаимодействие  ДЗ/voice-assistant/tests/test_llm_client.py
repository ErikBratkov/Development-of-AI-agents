import openai

from app.llm_client import is_retryable


def make_error(status: int | None) -> openai.OpenAIError:
    """Собирает ошибку openai с нужным статусом ответа"""
    exc = openai.OpenAIError("тестовая ошибка")
    if status is not None:
        exc.status_code = status
    return exc


def test_is_retryable_network_error() -> None:
    """Ошибку без статуса (сеть, таймаут) имеет смысл повторить"""
    assert is_retryable(make_error(None))


def test_is_retryable_rate_limit_and_server_errors() -> None:
    """429 и 5xx повторяем - лимит и сервер могут отпустить"""
    assert is_retryable(make_error(429))
    assert is_retryable(make_error(500))
    assert is_retryable(make_error(503))


def test_is_retryable_client_errors() -> None:
    """Клиентские 4xx повторять бесполезно"""
    assert not is_retryable(make_error(400))
    assert not is_retryable(make_error(401))
    assert not is_retryable(make_error(404))
