"""Bounded retries for transient Jira REST failures."""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

LOGGER = logging.getLogger(__name__)

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
        start = time.perf_counter()
        try:
            response = client.request(method, url, **kwargs)
        except httpx.RequestError as exc:
            attempts_used = attempt + 1
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            _log_outbound_http(
                method=method,
                url=url,
                status_code=None,
                attempts=attempts_used,
                duration_ms=duration_ms,
                error=str(exc),
            )
            if is_retriable_request_error(exc) and attempt < max_retries:
                time.sleep(_backoff_seconds(attempt))
                continue
            if is_retriable_request_error(exc):
                raise TransportRetriesExhausted(attempts_used, exc) from exc
            raise
        attempts_used = attempt + 1
        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        _log_outbound_http(
            method=method,
            url=url,
            status_code=response.status_code,
            attempts=attempts_used,
            duration_ms=duration_ms,
        )
        if not response.is_error:
            return response, attempts_used
        if not is_retriable_http_status(response.status_code) or attempt >= max_retries:
            return response, attempts_used
        time.sleep(_backoff_seconds(attempt))
    raise RuntimeError("request_with_retries: unreachable")


def _log_outbound_http(
    *,
    method: str,
    url: str,
    status_code: int | None,
    attempts: int,
    duration_ms: float,
    error: str | None = None,
) -> None:
    extra: dict[str, object] = {
        "event_type": "outbound_http",
        "method": method,
        "url": url,
        "attempts": attempts,
        "duration_ms": duration_ms,
    }
    if status_code is not None:
        extra["status_code"] = status_code
    if error is not None:
        extra["error"] = error
        LOGGER.warning(
            "outbound_http method=%s url=%s attempts=%d duration_ms=%.2f error=%s",
            method,
            url,
            attempts,
            duration_ms,
            error,
            extra=extra,
        )
        return
    level = logging.WARNING if status_code is not None and status_code >= 400 else logging.INFO
    LOGGER.log(
        level,
        "outbound_http method=%s url=%s status=%d attempts=%d duration_ms=%.2f",
        method,
        url,
        status_code,
        attempts,
        duration_ms,
        extra=extra,
    )
