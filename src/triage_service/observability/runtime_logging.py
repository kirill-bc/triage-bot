"""Configure process-wide logging and HTTP request log emission for container runs."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import logging
from time import perf_counter

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

LOGGER = logging.getLogger(__name__)

_LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
_LOG_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S%z"
_QUIET_HTTP_CLIENT_LOGGERS = ("httpx", "httpcore", "urllib3")
_RUNTIME_CONFIGURED = False


def reset_runtime_logging_for_tests() -> None:
    """Clear root handlers and config flag so unit tests can reconfigure logging."""
    global _RUNTIME_CONFIGURED
    root = logging.getLogger()
    for handler in root.handlers[:]:
        root.removeHandler(handler)
        handler.close()
    root.setLevel(logging.WARNING)
    _RUNTIME_CONFIGURED = False


def configure_runtime_logging(
    *,
    log_level: str = "INFO",
    http_client_log_level: str = "WARNING",
    force: bool = False,
) -> None:
    """Initialize stdout logging for uvicorn/container execution.

    Idempotent by default so repeated app factory calls in tests do not duplicate handlers.
    """
    global _RUNTIME_CONFIGURED
    if _RUNTIME_CONFIGURED and not force:
        return

    level = getattr(logging, log_level.upper(), logging.INFO)
    client_level = getattr(logging, http_client_log_level.upper(), logging.WARNING)

    logging.basicConfig(
        level=level,
        format=_LOG_FORMAT,
        datefmt=_LOG_DATE_FORMAT,
        force=True,
    )
    for name in _QUIET_HTTP_CLIENT_LOGGERS:
        logging.getLogger(name).setLevel(client_level)
    logging.getLogger("uvicorn.access").setLevel(level)
    logging.getLogger("uvicorn.error").setLevel(level)

    _RUNTIME_CONFIGURED = True
    LOGGER.info("runtime_logging_configured log_level=%s", log_level.upper())


class HttpAccessLogMiddleware(BaseHTTPMiddleware):
    """Log inbound HTTP requests with method, path, status, and latency."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        start = perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = round((perf_counter() - start) * 1000, 2)
            LOGGER.exception(
                "http_request_failed method=%s path=%s duration_ms=%.2f",
                request.method,
                request.url.path,
                duration_ms,
                extra={
                    "event_type": "http_request",
                    "method": request.method,
                    "path": request.url.path,
                    "duration_ms": duration_ms,
                },
            )
            raise
        duration_ms = round((perf_counter() - start) * 1000, 2)
        log = LOGGER.warning if response.status_code >= 400 else LOGGER.info
        log(
            "http_request method=%s path=%s status=%d duration_ms=%.2f",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            extra={
                "event_type": "http_request",
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            },
        )
        return response
