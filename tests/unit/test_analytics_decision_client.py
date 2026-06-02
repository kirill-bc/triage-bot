"""Unit tests for analytics decision-event HTTP client."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from triage_service.core.settings import AppSettings
from triage_service.observability.audit_events import TriageCompletedAuditEvent


def _settings(
    monkeypatch: pytest.MonkeyPatch,
    *,
    url: str | None = "http://dashboard.test/api/v1",
    token: str | None = "analytics-secret",
) -> AppSettings:
    monkeypatch.setenv("JIRA_API_KEY", "jira-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    monkeypatch.setenv("TRIAGE_WEBHOOK_TOKEN", "webhook-token")
    if url is None:
        monkeypatch.delenv("ANALYTICS_DASHBOARD_URL", raising=False)
    else:
        monkeypatch.setenv("ANALYTICS_DASHBOARD_URL", url)
    if token is None:
        monkeypatch.delenv("ANALYTICS_TOKEN", raising=False)
    else:
        monkeypatch.setenv("ANALYTICS_TOKEN", token)
    from triage_service.core.settings import load_settings

    return load_settings()


def _completed_event(**telemetry: object) -> TriageCompletedAuditEvent:
    return TriageCompletedAuditEvent(
        event_type="triage_completed",
        run_id="run-abc",
        issue_key="TJC-1",
        project="TJC",
        source="bug_created",
        recommended_issue_type="Story",
        recommended_priority=None,
        confidence=0.82,
        reason="Not a defect.",
        telemetry={
            "intake_issue_type": "Bug",
            "intake_priority": "P2",
            **telemetry,
        },
    )


@pytest.mark.unit
def test_build_decision_payload_maps_story_recommendation_with_null_priority() -> None:
    from triage_service.adapters.analytics_decision_client import build_decision_payload

    occurred = datetime(2026, 6, 2, 13, 0, tzinfo=timezone.utc)
    payload = build_decision_payload(
        _completed_event(),
        applied_type_change=False,
        applied_priority_change=False,
        inference_cost_usd=0.018,
        occurred_at=occurred,
    )
    assert payload == {
        "event_type": "triage_completed",
        "run_id": "run-abc",
        "issue_key": "TJC-1",
        "project": "TJC",
        "source": "bug_created",
        "intake_issue_type": "Bug",
        "intake_priority": "P2",
        "recommended_issue_type": "Story",
        "recommended_priority": None,
        "applied_type_change": False,
        "applied_priority_change": False,
        "confidence": 0.82,
        "inference_cost_usd": 0.018,
        "reason": "Not a defect.",
        "occurred_at": "2026-06-02T13:00:00+00:00",
    }


@pytest.mark.unit
def test_build_decision_payload_omits_inference_cost_when_none() -> None:
    from triage_service.adapters.analytics_decision_client import build_decision_payload

    payload = build_decision_payload(
        _completed_event(intake_issue_type="Story", intake_priority=None),
        applied_type_change=False,
        applied_priority_change=False,
        inference_cost_usd=None,
        occurred_at=datetime(2026, 6, 2, 13, 0, tzinfo=timezone.utc),
    )
    assert payload["intake_issue_type"] == "Story"
    assert payload["intake_priority"] is None
    assert "inference_cost_usd" not in payload


@pytest.mark.unit
def test_noop_client_skips_http_when_url_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    from triage_service.adapters.analytics_decision_client import build_analytics_decision_client

    settings = _settings(monkeypatch, url=None)
    client = build_analytics_decision_client(settings)
    with patch("httpx.Client.post") as post_mock:
        client.emit_completed_decision(
            _completed_event(),
            applied_type_change=False,
            applied_priority_change=False,
            inference_cost_usd=None,
        )
        post_mock.assert_not_called()


@pytest.mark.unit
def test_http_client_posts_payload_with_auth_header(monkeypatch: pytest.MonkeyPatch) -> None:
    from triage_service.adapters.analytics_decision_client import build_analytics_decision_client

    settings = _settings(monkeypatch)
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = request.content.decode()
        return httpx.Response(201)

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as http_client:
        client = build_analytics_decision_client(settings, client=http_client)
        client.emit_completed_decision(
            _completed_event(),
            applied_type_change=True,
            applied_priority_change=False,
            inference_cost_usd=0.01,
        )

    assert captured["url"] == "http://dashboard.test/api/v1/decisions"
    assert captured["headers"]["x-analytics-token"] == "analytics-secret"
    body = __import__("json").loads(captured["body"])
    assert body["applied_type_change"] is True
    assert body["run_id"] == "run-abc"


@pytest.mark.unit
def test_http_client_swallows_transport_errors(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from triage_service.adapters.analytics_decision_client import build_analytics_decision_client

    settings = _settings(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as http_client:
        client = build_analytics_decision_client(settings, client=http_client)
        with caplog.at_level("WARNING"):
            client.emit_completed_decision(
                _completed_event(),
                applied_type_change=False,
                applied_priority_change=False,
                inference_cost_usd=None,
            )

    assert any("analytics_decision_post_failed" in r.message for r in caplog.records)


@pytest.mark.integration
def test_http_client_posts_against_mock_server(monkeypatch: pytest.MonkeyPatch) -> None:
    from triage_service.adapters.analytics_decision_client import build_analytics_decision_client

    settings = _settings(monkeypatch)
    received: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        received.append(request)
        return httpx.Response(200)

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport, timeout=1.0) as http_client:
        client = build_analytics_decision_client(settings, client=http_client)
        client.emit_completed_decision(
            _completed_event(),
            applied_type_change=False,
            applied_priority_change=False,
            inference_cost_usd=0.005,
        )

    assert len(received) == 1
    assert received[0].method == "POST"
