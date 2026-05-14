"""Bounded retries for transient Jira REST failures."""

from __future__ import annotations

import time
from typing import Any

import httpx

_RETRIABLE_STATUSES = frozenset({429, 502, 503, 504})


class TransportRetriesExhausted(Exception):
    """Raised when retriable :class:`httpx.RequestError` failures exhaust all attempts."""

    def __init__(self, attempts: int, cause: httpx.RequestError) -> None:
        self.attempts = attempts
        self.cause = cause
        super().__init__(str(cause))


def is_retriable_http_status(status_code: int) -> bool:
    return status_code in _RETRIABLE_STATUSES


def is_retriable_request_error(exc: BaseException) -> bool:
    return isinstance(
        exc,
        (
            httpx.ConnectError,
            httpx.RemoteProtocolError,
            httpx.TimeoutException,
        ),
    )


def classify_transport_request_error(exc: httpx.RequestError) -> tuple[bool | None, str]:
    """Return ``(transport_timeout, transport_error_kind)`` for audit/log classification."""
    if isinstance(exc, httpx.TimeoutException):
        return True, "timeout"
    if isinstance(exc, httpx.ConnectError):
        return False, "connect_error"
    if isinstance(exc, httpx.RemoteProtocolError):
        return False, "protocol_error"
    return False, "request_error"


def _backoff_seconds(attempt: int) -> float:
    return float(min(2.0, 0.25 * (2**attempt)))


def request_with_retries(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    max_retries: int,
    **kwargs: Any,
) -> tuple[httpx.Response, int]:
    """Issue ``method``/``url`` with up to ``max_retries`` extra tries on transient errors.

    Returns ``(response, attempts)`` where ``attempts`` is the number of HTTP requests issued
    (including the terminal attempt).

    Raises :class:`TransportRetriesExhausted` when retriable transport errors exhaust retries.
    """
    for attempt in range(max_retries + 1):
        try:
            response = client.request(method, url, **kwargs)
        except httpx.RequestError as exc:
            attempts_used = attempt + 1
            if is_retriable_request_error(exc) and attempt < max_retries:
                time.sleep(_backoff_seconds(attempt))
                continue
            if is_retriable_request_error(exc):
                raise TransportRetriesExhausted(attempts_used, exc) from exc
            raise
        attempts_used = attempt + 1
        if not response.is_error:
            return response, attempts_used
        if not is_retriable_http_status(response.status_code) or attempt >= max_retries:
            return response, attempts_used
        time.sleep(_backoff_seconds(attempt))
    raise RuntimeError("request_with_retries: unreachable")
