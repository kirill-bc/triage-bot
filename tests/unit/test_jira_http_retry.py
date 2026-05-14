"""Unit tests for Jira HTTP retry classification helpers."""

from __future__ import annotations

from collections.abc import Callable
from unittest.mock import MagicMock

import httpx
import pytest

from triage_service.adapters.jira_http_retry import (
    TransportRetriesExhausted,
    classify_transport_request_error,
    is_retriable_http_status,
    is_retriable_request_error,
    request_with_retries,
)


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (429, True),
        (502, True),
        (503, True),
        (504, True),
        (404, False),
        (500, False),
        (200, False),
    ],
)
@pytest.mark.unit
def test_is_retriable_http_status_matches_policy(status: int, expected: bool) -> None:
    assert is_retriable_http_status(status) is expected


@pytest.mark.unit
def test_is_retriable_request_error_true_for_connect_and_timeouts() -> None:
    req = httpx.Request("GET", "https://example.com")
    assert is_retriable_request_error(httpx.ConnectError("x", request=req))
    assert is_retriable_request_error(httpx.ReadTimeout("x", request=req))
    assert is_retriable_request_error(httpx.RemoteProtocolError("x", request=req))


@pytest.mark.unit
def test_is_retriable_request_error_false_for_value_error() -> None:
    assert not is_retriable_request_error(ValueError("not transport"))


@pytest.fixture
def sample_request() -> httpx.Request:
    return httpx.Request("GET", "https://example.com/issue")


@pytest.mark.parametrize(
    ("exc_factory", "expect_timeout", "expect_kind"),
    [
        (
            lambda r: httpx.ReadTimeout("read slow", request=r),
            True,
            "timeout",
        ),
        (
            lambda r: httpx.ConnectTimeout("connect slow", request=r),
            True,
            "timeout",
        ),
        (
            lambda r: httpx.ConnectError("refused", request=r),
            False,
            "connect_error",
        ),
        (
            lambda r: httpx.RemoteProtocolError("bad peer", request=r),
            False,
            "protocol_error",
        ),
    ],
)
@pytest.mark.unit
def test_classify_transport_request_error_timeout_and_kind_mapping(
    sample_request: httpx.Request,
    exc_factory: Callable[[httpx.Request], httpx.RequestError],
    expect_timeout: bool,
    expect_kind: str,
) -> None:
    exc = exc_factory(sample_request)
    assert isinstance(exc, httpx.RequestError)
    timeout, kind = classify_transport_request_error(exc)
    assert timeout is expect_timeout
    assert kind == expect_kind


@pytest.mark.unit
def test_classify_transport_request_error_unknown_request_error_kind(
    sample_request: httpx.Request,
) -> None:
    class OddTransportError(httpx.RequestError):
        pass

    exc = OddTransportError("odd", request=sample_request)
    timeout, kind = classify_transport_request_error(exc)
    assert timeout is False
    assert kind == "request_error"


@pytest.mark.unit
def test_request_with_retries_retries_transient_http_then_returns_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "triage_service.adapters.jira_http_retry.time.sleep",
        lambda *_a, **_k: None,
    )
    calls = {"n": 0}

    def do_request(
        method: str,
        url: str,
        **kwargs: object,
    ) -> httpx.Response:
        _ = (method, url, kwargs)
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, text="retry me")
        return httpx.Response(200, text="ok")

    client = MagicMock()
    client.request = do_request

    response, attempts = request_with_retries(
        client,
        "GET",
        "https://example.com/x",
        max_retries=2,
    )
    assert response.status_code == 200
    assert attempts == 2
    assert calls["n"] == 2


@pytest.mark.unit
def test_request_with_retries_does_not_retry_non_retriable_http_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "triage_service.adapters.jira_http_retry.time.sleep",
        lambda *_a, **_k: None,
    )
    calls = {"n": 0}

    def do_request(
        method: str,
        url: str,
        **kwargs: object,
    ) -> httpx.Response:
        _ = (method, url, kwargs)
        calls["n"] += 1
        return httpx.Response(500, text="no retry")

    client = MagicMock()
    client.request = do_request

    response, attempts = request_with_retries(
        client,
        "GET",
        "https://example.com/x",
        max_retries=2,
    )
    assert response.status_code == 500
    assert attempts == 1
    assert calls["n"] == 1


@pytest.mark.unit
def test_request_with_retries_raises_transport_retries_exhausted_with_attempt_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "triage_service.adapters.jira_http_retry.time.sleep",
        lambda *_a, **_k: None,
    )
    req = httpx.Request("GET", "https://example.com/issue")
    calls = {"n": 0}

    def always_connect_error(
        method: str,
        url: str,
        **kwargs: object,
    ) -> httpx.Response:  # pragma: no cover - signature matches httpx
        _ = (method, url, kwargs)
        calls["n"] += 1
        raise httpx.ConnectError("down", request=req)

    client = MagicMock()
    client.request = always_connect_error

    with pytest.raises(TransportRetriesExhausted) as exc:
        request_with_retries(client, "GET", "https://example.com/issue", max_retries=2)
    assert exc.value.attempts == 3
    assert calls["n"] == 3
    assert isinstance(exc.value.cause, httpx.ConnectError)
