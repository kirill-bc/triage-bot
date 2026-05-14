"""Unit tests for Jira HTTP retry classification helpers."""

from __future__ import annotations

import httpx
import pytest

from triage_service.adapters.jira_http_retry import (
    is_retriable_http_status,
    is_retriable_request_error,
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


@pytest.mark.unit
def test_is_retriable_request_error_false_for_value_error() -> None:
    assert not is_retriable_request_error(ValueError("not transport"))
