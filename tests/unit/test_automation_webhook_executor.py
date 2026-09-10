"""Unit tests for Jira Automation callback outcome delivery."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
import pytest

from triage_service.adapters.automation_webhook_executor import (
    AutomationCallbackError,
    AutomationWebhookTriageActionExecutor,
)
from triage_service.adapters.jira_issue_fetcher import FetchedIssue
from triage_service.core.settings import AppSettings
from triage_service.core.triage_fallback import TriageFailure
from triage_service.core.triage_recommendation_parser import TriageRecommendation
from triage_service.observability.audit_events import TriageAuditEvent

_WEBHOOK_URL = (
    "https://api-private.atlassian.com/automation/webhooks/jira/cloud/secret-hook-id"
)


class _RecordingAuditStore:
    """Collects audit events emitted during outcome delivery."""

    def __init__(self) -> None:
        self.events: list[TriageAuditEvent] = []

    def record(self, event: TriageAuditEvent) -> None:
        self.events.append(event)


def _settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> AppSettings:
    monkeypatch.setenv("JIRA_API_KEY", "jira-api-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-token")
    monkeypatch.setenv("TRIAGE_WEBHOOK_TOKEN", "triage-token")
    monkeypatch.setenv("TRIAGE_JIRA_APPLY_MODE", "automation_webhook")
    monkeypatch.setenv("JIRA_AUTOMATION_WEBHOOK_URL", _WEBHOOK_URL)
    monkeypatch.setenv("JIRA_AUTOMATION_WEBHOOK_TOKEN", "automation-secret")
    for key in (
        "TRIAGE_AUTO_APPLY_DEESCALATION",
        "TRIAGE_AUTO_APPLY_ESCALATION",
        "TRIAGE_AUTO_APPLY_BUG_TO_STORY",
    ):
        if key not in env:
            monkeypatch.setenv(key, "false")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return AppSettings()


def _issue(**overrides: Any) -> FetchedIssue:
    values: dict[str, Any] = {
        "issue_key": "TJC-1",
        "summary": "Summary",
        "description": None,
        "issue_type": "Bug",
        "priority": "P2",
        "reporter": "Alice",
    }
    values.update(overrides)
    return FetchedIssue.model_validate(values)


def _recommendation(**overrides: Any) -> TriageRecommendation:
    values: dict[str, Any] = {
        "recommended_issue_type": "Bug",
        "recommended_priority": "P2",
        "confidence": 0.8,
        "reason": "Matches policy.",
    }
    values.update(overrides)
    return TriageRecommendation.model_validate(values)


@pytest.mark.unit
def test_executor_posts_versioned_payload_with_token_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(monkeypatch)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        executor = AutomationWebhookTriageActionExecutor(settings, client=client)
        executor.apply_triage_outcome(
            issue=_issue(priority="P1", reporter_account_id="account-123"),
            issue_key="TJC-1",
            project="TJC",
            source="bug_created",
            outcome=_recommendation(recommended_priority="P3", reason="Workaround exists."),
            run_id="run-1",
        )

    assert len(requests) == 1
    assert str(requests[0].url) == _WEBHOOK_URL
    assert requests[0].method == "POST"
    assert requests[0].headers["X-Automation-Webhook-Token"] == "automation-secret"

    payload = json.loads(requests[0].content.decode())
    assert payload["payload_version"] == 1
    assert payload["issues"] == ["TJC-1"]
    assert payload["run_id"] == "run-1"
    assert payload["issue_key"] == "TJC-1"
    assert payload["project"] == "TJC"
    assert payload["source"] == "bug_created"
    assert payload["recommendation"] == {
        "issue_type": "Bug",
        "priority": "P3",
        "confidence": 0.8,
    }
    assert payload["labels"] == ["triagebot-reviewed", "triagebot-priority-mismatch"]
    assert payload["comment"]["post"] is True
    assert payload["comment"]["body"].startswith("[~accountid:account-123]")
    assert "Change ticket Priority from P1 to P3." in payload["comment"]["body"]
    assert payload["actions"] == {"apply_bug_to_story": False, "apply_priority": None}


@pytest.mark.unit
def test_executor_redacts_webhook_path_from_outbound_log(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = _settings(monkeypatch)
    caplog.set_level(logging.INFO, logger="triage_service.adapters.jira_http_retry")

    transport = httpx.MockTransport(lambda request: httpx.Response(200, request=request))
    with httpx.Client(transport=transport) as client:
        executor = AutomationWebhookTriageActionExecutor(settings, client=client)
        executor.apply_triage_outcome(
            issue=_issue(),
            issue_key="TJC-1",
            project="TJC",
            source="bug_created",
            outcome=_recommendation(),
            run_id="run-1",
        )

    record = [
        item for item in caplog.records if str(item.msg).startswith("outbound_http")
    ][-1]
    assert getattr(record, "url") == "https://api-private.atlassian.com"
    assert "secret-hook-id" not in record.getMessage()


@pytest.mark.unit
def test_executor_marks_comment_not_posted_when_no_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(monkeypatch)
    posted: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        posted.append(json.loads(request.content.decode()))
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        executor = AutomationWebhookTriageActionExecutor(settings, client=client)
        executor.apply_triage_outcome(
            issue=_issue(),
            issue_key="TJC-1",
            project="TJC",
            source="bug_created",
            outcome=_recommendation(),
            run_id="run-1",
        )

    assert len(posted) == 1
    assert posted[0]["labels"] == ["triagebot-reviewed"]
    assert posted[0]["comment"] == {"post": False, "body": None}


@pytest.mark.unit
def test_executor_marks_comment_not_posted_when_comments_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(monkeypatch)
    posted: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        posted.append(json.loads(request.content.decode()))
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        executor = AutomationWebhookTriageActionExecutor(
            settings,
            client=client,
            post_mismatch_comments=False,
        )
        executor.apply_triage_outcome(
            issue=_issue(priority="P1"),
            issue_key="TJC-1",
            project="TJC",
            source="bug_created",
            outcome=_recommendation(recommended_priority="P3"),
            run_id="run-1",
        )

    assert posted[0]["labels"] == ["triagebot-reviewed", "triagebot-priority-mismatch"]
    assert posted[0]["comment"] == {"post": False, "body": None}


@pytest.mark.unit
@pytest.mark.parametrize(
    "outcome",
    [
        TriageFailure(category="inference_failed", message="upstream down"),
        None,
    ],
)
def test_executor_skips_callback_without_applicable_outcome(
    monkeypatch: pytest.MonkeyPatch,
    outcome: TriageFailure | None,
) -> None:
    """Failure outcomes and missing issues mirror the direct path's no-write semantics."""
    settings = _settings(monkeypatch)
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        executor = AutomationWebhookTriageActionExecutor(settings, client=client)
        applied = executor.apply_triage_outcome(
            issue=None if outcome is None else _issue(),
            issue_key="TJC-1",
            project="TJC",
            source="bug_created",
            outcome=outcome if outcome is not None else _recommendation(),
            run_id="run-1",
        )

    assert calls == []
    assert applied.applied_type_change is False
    assert applied.applied_priority_change is False


@pytest.mark.unit
def test_executor_directs_priority_change_when_escalation_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(monkeypatch, TRIAGE_AUTO_APPLY_ESCALATION="true")
    posted: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        posted.append(json.loads(request.content.decode()))
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        executor = AutomationWebhookTriageActionExecutor(settings, client=client)
        applied = executor.apply_triage_outcome(
            issue=_issue(priority="P3"),
            issue_key="TJC-1",
            project="TJC",
            source="bug_created",
            outcome=_recommendation(recommended_priority="P1", reason="Client blocked."),
            run_id="run-1",
        )

    assert posted[0]["actions"] == {
        "apply_bug_to_story": False,
        "apply_priority": {"from": "P3", "to": "P1"},
    }
    assert applied.applied_priority_change is True
    assert applied.applied_type_change is False
    assert "The ticket Priority was changed from P3 to P1." in posted[0]["comment"]["body"]


@pytest.mark.unit
def test_executor_directs_bug_to_story_when_flag_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(monkeypatch, TRIAGE_AUTO_APPLY_BUG_TO_STORY="true")
    posted: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        posted.append(json.loads(request.content.decode()))
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        executor = AutomationWebhookTriageActionExecutor(settings, client=client)
        applied = executor.apply_triage_outcome(
            issue=_issue(issue_type="Bug"),
            issue_key="TJC-1",
            project="TJC",
            source="manual_trigger",
            outcome=_recommendation(
                recommended_issue_type="Story",
                recommended_priority=None,
                reason="Enhancement request.",
            ),
            run_id="run-1",
        )

    assert posted[0]["recommendation"] == {
        "issue_type": "Story",
        "priority": None,
        "confidence": 0.8,
    }
    assert posted[0]["actions"] == {"apply_bug_to_story": True, "apply_priority": None}
    assert applied.applied_type_change is True
    assert applied.applied_priority_change is False
    assert "The issue type was changed from Bug to Story." in posted[0]["comment"]["body"]


@pytest.mark.unit
def test_executor_raises_automation_callback_error_on_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(monkeypatch)

    transport = httpx.MockTransport(lambda _r: httpx.Response(400, text="bad payload"))
    with httpx.Client(transport=transport) as client:
        executor = AutomationWebhookTriageActionExecutor(settings, client=client)
        with pytest.raises(AutomationCallbackError, match="400"):
            executor.apply_triage_outcome(
                issue=_issue(),
                issue_key="TJC-1",
                project="TJC",
                source="bug_created",
                outcome=_recommendation(),
                run_id="run-1",
            )


@pytest.mark.unit
def test_executor_raises_automation_callback_error_when_url_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JIRA_API_KEY", "jira-api-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-token")
    monkeypatch.setenv("TRIAGE_WEBHOOK_TOKEN", "triage-token")
    monkeypatch.delenv("JIRA_AUTOMATION_WEBHOOK_URL", raising=False)
    monkeypatch.setenv("TRIAGE_JIRA_APPLY_MODE", "direct")
    settings = AppSettings()

    transport = httpx.MockTransport(lambda _r: httpx.Response(200, json={}))
    with httpx.Client(transport=transport) as client:
        executor = AutomationWebhookTriageActionExecutor(settings, client=client)
        with pytest.raises(AutomationCallbackError, match="JIRA_AUTOMATION_WEBHOOK_URL"):
            executor.apply_triage_outcome(
                issue=_issue(),
                issue_key="TJC-1",
                project="TJC",
                source="bug_created",
                outcome=_recommendation(),
                run_id="run-1",
            )


@pytest.mark.unit
def test_executor_does_not_retry_callback_on_transient_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rule B is not idempotent; a lost 200 plus retry would duplicate comments."""
    monkeypatch.setattr(
        "triage_service.adapters.jira_http_retry.time.sleep",
        lambda _s: None,
    )
    settings = _settings(monkeypatch, TRIAGE_JIRA_HTTP_MAX_RETRIES="2")
    attempts = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(503, text="gateway")

    transport = httpx.MockTransport(handler)
    audit_store = _RecordingAuditStore()
    with httpx.Client(transport=transport) as client:
        executor = AutomationWebhookTriageActionExecutor(
            settings,
            client=client,
            audit_store=audit_store,
        )
        with pytest.raises(AutomationCallbackError, match="503"):
            executor.apply_triage_outcome(
                issue=_issue(),
                issue_key="TJC-1",
                project="TJC",
                source="bug_created",
                outcome=_recommendation(),
                run_id="run-1",
            )

    assert attempts["n"] == 1
    delivered = [e for e in audit_store.events if e.event_type == "outcome_delivered"]
    assert len(delivered) == 1
    assert delivered[0].delivered is False
    assert delivered[0].attempts == 1


@pytest.mark.unit
def test_executor_records_outcome_delivered_audit_event_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(monkeypatch)
    audit_store = _RecordingAuditStore()

    transport = httpx.MockTransport(lambda _r: httpx.Response(200, json={}))
    with httpx.Client(transport=transport) as client:
        executor = AutomationWebhookTriageActionExecutor(
            settings,
            client=client,
            audit_store=audit_store,
        )
        executor.apply_triage_outcome(
            issue=_issue(),
            issue_key="TJC-1",
            project="TJC",
            source="bug_created",
            outcome=_recommendation(),
            run_id="run-1",
        )

    assert len(audit_store.events) == 1
    event = audit_store.events[0]
    assert event.event_type == "outcome_delivered"
    assert event.run_id == "run-1"
    assert event.issue_key == "TJC-1"
    assert event.project == "TJC"
    assert event.source == "bug_created"
    assert event.delivery_mode == "automation_webhook"
    assert event.delivered is True
    assert event.http_status == 200
    assert event.attempts == 1
    assert event.failure is None


@pytest.mark.unit
def test_executor_records_outcome_delivered_audit_event_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(monkeypatch)
    audit_store = _RecordingAuditStore()

    transport = httpx.MockTransport(lambda _r: httpx.Response(400, text="bad payload"))
    with httpx.Client(transport=transport) as client:
        executor = AutomationWebhookTriageActionExecutor(
            settings,
            client=client,
            audit_store=audit_store,
        )
        with pytest.raises(AutomationCallbackError):
            executor.apply_triage_outcome(
                issue=_issue(),
                issue_key="TJC-1",
                project="TJC",
                source="bug_created",
                outcome=_recommendation(),
                run_id="run-1",
            )

    assert len(audit_store.events) == 1
    event = audit_store.events[0]
    assert event.event_type == "outcome_delivered"
    assert event.delivered is False
    assert event.http_status == 400
    assert event.failure is not None


@pytest.mark.unit
def test_executor_prefers_request_url_over_settings_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Project-scoped Rule B rules each have their own webhook URL."""
    settings = _settings(monkeypatch)
    per_project_url = "https://api-private.atlassian.com/automation/webhooks/jira/a/one/two"
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        executor = AutomationWebhookTriageActionExecutor(
            settings,
            client=client,
            webhook_url=per_project_url,
        )
        executor.apply_triage_outcome(
            issue=_issue(),
            issue_key="TJC-1",
            project="TJC",
            source="bug_created",
            outcome=_recommendation(),
            run_id="run-1",
        )

    assert str(requests[0].url) == per_project_url


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad_url",
    [
        "https://evil.example.com/hooks/abc",
        "https://automation.atlassian.com/pro/hooks/abc",
        "http://automation.atlassian.com/pro/hooks/abc",
        "https://automation.atlassian.com.evil.example.com/hooks/abc",
    ],
)
def test_executor_rejects_request_url_outside_atlassian_hosts(
    monkeypatch: pytest.MonkeyPatch,
    bad_url: str,
) -> None:
    """A caller-supplied URL must not aim the callback POST at an arbitrary host."""
    settings = _settings(monkeypatch)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        executor = AutomationWebhookTriageActionExecutor(
            settings,
            client=client,
            webhook_url=bad_url,
        )
        with pytest.raises(AutomationCallbackError):
            executor.apply_triage_outcome(
                issue=_issue(),
                issue_key="TJC-1",
                project="TJC",
                source="bug_created",
                outcome=_recommendation(),
                run_id="run-1",
            )

    assert requests == []


@pytest.mark.unit
def test_executor_prefers_request_token_over_settings_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Per-project Rule B secrets travel in the triage request, not the service env."""
    settings = _settings(monkeypatch)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        executor = AutomationWebhookTriageActionExecutor(
            settings,
            client=client,
            webhook_token="per-project-token",
        )
        executor.apply_triage_outcome(
            issue=_issue(),
            issue_key="TJC-1",
            project="TJC",
            source="bug_created",
            outcome=_recommendation(),
            run_id="run-1",
        )

    assert requests[0].headers["X-Automation-Webhook-Token"] == "per-project-token"


@pytest.mark.unit
@pytest.mark.parametrize("request_token", [None, "", "   "])
def test_executor_falls_back_to_settings_token_when_request_token_absent(
    monkeypatch: pytest.MonkeyPatch,
    request_token: str | None,
) -> None:
    settings = _settings(monkeypatch)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        executor = AutomationWebhookTriageActionExecutor(
            settings,
            client=client,
            webhook_token=request_token,
        )
        executor.apply_triage_outcome(
            issue=_issue(),
            issue_key="TJC-1",
            project="TJC",
            source="bug_created",
            outcome=_recommendation(),
            run_id="run-1",
        )

    assert requests[0].headers["X-Automation-Webhook-Token"] == "automation-secret"


@pytest.mark.unit
def test_executor_omits_token_header_when_token_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _settings(monkeypatch)
    monkeypatch.delenv("JIRA_AUTOMATION_WEBHOOK_TOKEN", raising=False)
    settings = AppSettings()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        executor = AutomationWebhookTriageActionExecutor(settings, client=client)
        executor.apply_triage_outcome(
            issue=_issue(),
            issue_key="TJC-1",
            project="TJC",
            source="bug_created",
            outcome=_recommendation(),
            run_id="run-1",
        )

    assert "X-Automation-Webhook-Token" not in requests[0].headers
