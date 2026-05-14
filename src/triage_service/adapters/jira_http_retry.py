"""Bounded retries for transient Jira REST failures."""

from __future__ import annotations

import time
from typing import Any

import httpx

_RETRIABLE_STATUSES = frozenset({429, 502, 503, 504})


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
    """
    for attempt in range(max_retries + 1):
        try:
            response = client.request(method, url, **kwargs)
        except httpx.RequestError as exc:
            if not is_retriable_request_error(exc) or attempt >= max_retries:
                raise
            time.sleep(_backoff_seconds(attempt))
            continue
        attempts_used = attempt + 1
        if not response.is_error:
            return response, attempts_used
        if not is_retriable_http_status(response.status_code) or attempt >= max_retries:
            return response, attempts_used
        time.sleep(_backoff_seconds(attempt))
    raise RuntimeError("request_with_retries: unreachable")
