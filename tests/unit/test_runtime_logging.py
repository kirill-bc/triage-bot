"""Runtime logging configuration and HTTP access/outbound log emission."""

from __future__ import annotations

import logging

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from triage_service.adapters.jira_http_retry import request_with_retries
from triage_service.observability.runtime_logging import (
    HttpAccessLogMiddleware,
    configure_runtime_logging,
    reset_runtime_logging_for_tests,
)


@pytest.fixture(autouse=True)
def _reset_logging() -> None:
    reset_runtime_logging_for_tests()


@pytest.mark.unit
def test_configure_runtime_logging_sets_root_and_quiets_http_clients() -> None:
    configure_runtime_logging(log_level="DEBUG", force=True)

    assert logging.getLogger().level == logging.DEBUG
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING
    assert logging.getLogger("urllib3").level == logging.WARNING


@pytest.mark.unit
def test_configure_runtime_logging_is_idempotent_without_force() -> None:
    configure_runtime_logging(log_level="INFO", force=True)
    root_handlers = len(logging.getLogger().handlers)

    configure_runtime_logging(log_level="DEBUG", force=False)

    assert logging.getLogger().level == logging.INFO
    assert len(logging.getLogger().handlers) == root_handlers


@pytest.mark.unit
def test_http_access_log_middleware_logs_successful_request(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="triage_service.observability.runtime_logging")

    app = FastAPI()

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"ok": "true"}

    app.add_middleware(HttpAccessLogMiddleware)
    client = TestClient(app)
    response = client.get("/health")

    assert response.status_code == 200
    records = [r for r in caplog.records if str(r.msg).startswith("http_request ")]
    assert records
    rec = records[-1]
    assert getattr(rec, "method") == "GET"
    assert getattr(rec, "path") == "/health"
    assert getattr(rec, "status_code") == 200
    assert getattr(rec, "duration_ms") >= 0


@pytest.mark.unit
def test_http_access_log_middleware_logs_client_errors_at_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="triage_service.observability.runtime_logging")

    app = FastAPI()

    @app.get("/missing")
    def missing() -> None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="not found")

    app.add_middleware(HttpAccessLogMiddleware)
    client = TestClient(app)
    response = client.get("/missing")

    assert response.status_code == 404
    records = [r for r in caplog.records if str(r.msg).startswith("http_request ")]
    assert records
    assert records[-1].levelno == logging.WARNING
    assert getattr(records[-1], "status_code") == 404


@pytest.mark.unit
def test_http_access_log_middleware_logs_unhandled_exception(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.ERROR, logger="triage_service.observability.runtime_logging")

    app = FastAPI()

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("unexpected")

    app.add_middleware(HttpAccessLogMiddleware)
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/boom")

    assert response.status_code == 500
    failed = [r for r in caplog.records if str(r.msg).startswith("http_request_failed")]
    assert failed
    assert getattr(failed[-1], "method") == "GET"
    assert getattr(failed[-1], "path") == "/boom"


@pytest.mark.unit
def test_request_with_retries_logs_outbound_http(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="triage_service.adapters.jira_http_retry")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True}, request=request)

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    response, attempts = request_with_retries(
        client,
        "GET",
        "https://example.atlassian.net/rest/api/3/issue/TJC-1",
        max_retries=0,
    )

    assert response.status_code == 200
    assert attempts == 1
    records = [r for r in caplog.records if str(r.msg).startswith("outbound_http")]
    assert records
    rec = records[-1]
    assert getattr(rec, "method") == "GET"
    assert getattr(rec, "url") == "https://example.atlassian.net/rest/api/3/issue/TJC-1"
    assert getattr(rec, "status_code") == 200
    assert getattr(rec, "attempts") == 1
    assert getattr(rec, "duration_ms") >= 0


@pytest.mark.unit
def test_request_with_retries_logs_outbound_http_errors_at_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="triage_service.adapters.jira_http_retry")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="busy", request=request)

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    response, _ = request_with_retries(
        client,
        "POST",
        "https://openrouter.ai/api/v1/chat/completions",
        max_retries=0,
        json={"model": "x"},
    )

    assert response.status_code == 503
    records = [r for r in caplog.records if str(r.msg).startswith("outbound_http")]
    assert records
    assert records[-1].levelno == logging.WARNING
    assert getattr(records[-1], "status_code") == 503
