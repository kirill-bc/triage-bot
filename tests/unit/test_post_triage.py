"""POST /triage request and response contract.

The request body carries a ``source`` annotation: ``bug_created`` or
``priority_changed`` for Jira Automation triggers, or ``manual_trigger`` for the
local runner.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from triage_service.api.triage_api import create_app
from triage_service.core.triage_fallback import fallback_for_exception
from triage_service.core.triage_handler import TriageRunner, TriageSyncResult
from triage_service.core.triage_recommendation_parser import TriageRecommendation

_TRIAGE_TOKEN = "test-triage-token"


class _StubRunner:
    """Returns a fixed recommendation without touching Jira or OpenRouter."""

    def run_sync(
        self,
        issue_key: str,
        project: str,
        source: str,
        *,
        run_id: str,
    ) -> TriageSyncResult:
        _ = run_id
        return TriageSyncResult(
            outcome=TriageRecommendation(
                recommended_issue_type="Story",
                recommended_priority=None,
                confidence=0.5,
                reason="acceptance stub",
            ),
        )


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(triage_handler_factory=lambda: _StubRunner()))


@pytest.fixture(autouse=True)
def _configure_triage_webhook_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRIAGE_WEBHOOK_TOKEN", _TRIAGE_TOKEN)


def _auth_headers(*, token: str = _TRIAGE_TOKEN) -> dict[str, str]:
    return {"X-Triage-Token": token}


@pytest.mark.unit
def test_post_triage_returns_401_when_token_missing(client: TestClient) -> None:
    payload = {"issue_key": "TJC-9", "project": "TJC", "source": "manual_trigger"}
    response = client.post("/triage", json=payload)
    assert response.status_code == 401


@pytest.mark.unit
def test_post_triage_returns_401_when_token_invalid(client: TestClient) -> None:
    payload = {"issue_key": "TJC-9", "project": "TJC", "source": "manual_trigger"}
    response = client.post("/triage", json=payload, headers=_auth_headers(token="wrong-token"))
    assert response.status_code == 401


@pytest.mark.unit
def test_post_triage_returns_200_when_token_valid(client: TestClient) -> None:
    payload = {"issue_key": "TJC-9", "project": "TJC", "source": "manual_trigger"}
    response = client.post("/triage", json=payload, headers=_auth_headers())
    assert response.status_code == 200


@pytest.mark.unit
@pytest.mark.parametrize(
    ("request_mode", "expected_mode"),
    [
        ("automation_webhook", "automation_webhook"),
        ("direct", "direct"),
        (None, None),
    ],
)
def test_post_triage_passes_optional_apply_mode_to_default_handler(
    monkeypatch: pytest.MonkeyPatch,
    request_mode: str | None,
    expected_mode: str | None,
) -> None:
    monkeypatch.setenv("JIRA_API_KEY", "jira-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-token")
    observed: list[str | None] = []

    def _build(
        *,
        jira_apply_mode: str | None = None,
        jira_automation_webhook_token: str | None = None,
        jira_automation_webhook_url: str | None = None,
    ) -> _StubRunner:
        _ = (jira_automation_webhook_token, jira_automation_webhook_url)
        observed.append(jira_apply_mode)
        return _StubRunner()

    payload: dict[str, str] = {
        "issue_key": "TJC-9",
        "project": "TJC",
        "source": "bug_created",
    }
    if request_mode is not None:
        payload["jira_apply_mode"] = request_mode
    if request_mode == "automation_webhook":
        payload["jira_automation_webhook_url"] = (
            "https://api-private.atlassian.com/automation/webhooks/jira/cloud/hook"
        )

    with patch(
        "triage_service.api.triage_api.build_default_triage_handler",
        side_effect=_build,
    ):
        app_client = TestClient(create_app())
        response = app_client.post("/triage", json=payload, headers=_auth_headers())

    assert response.status_code == 200
    assert observed == [expected_mode]


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad_url",
    [
        "https://evil.example.com/hooks/abc",
        "https://automation.atlassian.com/pro/hooks/abc",
        "http://automation.atlassian.com/pro/hooks/abc",
        "not-a-url",
    ],
)
def test_post_triage_rejects_webhook_url_outside_atlassian_hosts(
    client: TestClient,
    bad_url: str,
) -> None:
    """Reject a hostile callback target before any inference spend."""
    response = client.post(
        "/triage",
        json={
            "issue_key": "TJC-9",
            "project": "TJC",
            "source": "bug_created",
            "jira_apply_mode": "automation_webhook",
            "jira_automation_webhook_url": bad_url,
        },
        headers=_auth_headers(),
    )

    assert response.status_code == 422


@pytest.mark.unit
def test_post_triage_forwards_webhook_token_from_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JIRA_API_KEY", "jira-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-token")
    observed: list[dict[str, object]] = []

    def _build(**kwargs: object) -> _StubRunner:
        observed.append(kwargs)
        return _StubRunner()

    with patch(
        "triage_service.api.triage_api.build_default_triage_handler",
        side_effect=_build,
    ):
        app_client = TestClient(create_app())
        response = app_client.post(
            "/triage",
            json={
                "issue_key": "TJC-9",
                "project": "TJC",
                "source": "bug_created",
                "jira_apply_mode": "automation_webhook",
                "jira_automation_webhook_url": (
                    "https://api-private.atlassian.com/automation/webhooks/jira/cloud/per-project"
                ),
            },
            headers={
                **_auth_headers(),
                "X-Jira-Automation-Webhook-Token": "per-project-token",
            },
        )

    assert response.status_code == 200
    assert observed == [
        {
            "jira_apply_mode": "automation_webhook",
            "jira_automation_webhook_token": "per-project-token",
            "jira_automation_webhook_url": (
                "https://api-private.atlassian.com/automation/webhooks/jira/cloud/per-project"
            ),
        },
    ]
    # The forwarded secret must never be echoed back to the caller.
    assert "per-project-token" not in response.text


@pytest.mark.unit
def test_post_triage_ignores_webhook_token_in_json_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rule B secrets belong in X-Jira-Automation-Webhook-Token, not the JSON body."""
    monkeypatch.setenv("JIRA_API_KEY", "jira-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-token")
    observed: list[dict[str, object]] = []

    def _build(**kwargs: object) -> _StubRunner:
        observed.append(kwargs)
        return _StubRunner()

    with patch(
        "triage_service.api.triage_api.build_default_triage_handler",
        side_effect=_build,
    ):
        app_client = TestClient(create_app())
        response = app_client.post(
            "/triage",
            json={
                "issue_key": "TJC-9",
                "project": "TJC",
                "source": "bug_created",
                "jira_apply_mode": "automation_webhook",
                "jira_automation_webhook_token": "body-token-must-be-ignored",
                "jira_automation_webhook_url": (
                    "https://api-private.atlassian.com/automation/webhooks/jira/cloud/hook"
                ),
            },
            headers=_auth_headers(),
        )

    assert response.status_code == 200
    assert observed[0]["jira_automation_webhook_token"] is None
    assert "body-token-must-be-ignored" not in response.text


@pytest.mark.unit
def test_post_triage_returns_422_when_webhook_mode_has_no_callback_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("JIRA_AUTOMATION_WEBHOOK_URL", raising=False)
    observed: list[dict[str, object]] = []

    def _build(**kwargs: object) -> _StubRunner:
        observed.append(kwargs)
        return _StubRunner()

    with patch(
        "triage_service.api.triage_api.build_default_triage_handler",
        side_effect=_build,
    ):
        app_client = TestClient(create_app())
        response = app_client.post(
            "/triage",
            json={
                "issue_key": "TJC-9",
                "project": "TJC",
                "source": "bug_created",
                "jira_apply_mode": "automation_webhook",
            },
            headers=_auth_headers(),
        )

    assert response.status_code == 422
    assert "callback URL is required" in response.json()["detail"]
    assert observed == []


@pytest.mark.unit
def test_post_triage_rejects_unknown_apply_mode(client: TestClient) -> None:
    response = client.post(
        "/triage",
        json={
            "issue_key": "TJC-9",
            "project": "TJC",
            "source": "bug_created",
            "jira_apply_mode": "carrier_pigeon",
        },
        headers=_auth_headers(),
    )

    assert response.status_code == 422


@pytest.mark.unit
def test_post_triage_response_includes_parseable_uuid_run_id(client: TestClient) -> None:
    payload = {"issue_key": "TJC-9", "project": "TJC", "source": "manual_trigger"}
    response = client.post("/triage", json=payload, headers=_auth_headers())
    assert response.status_code == 200
    data = response.json()
    assert "run_id" in data
    uuid.UUID(data["run_id"])


@pytest.mark.unit
def test_post_triage_run_id_propagated_to_runner_matches_response(client: TestClient) -> None:
    seen: list[str] = []

    class _CapturingRunner:
        def run_sync(
            self,
            issue_key: str,
            project: str,
            source: str,
            *,
            run_id: str,
        ) -> TriageSyncResult:
            _ = (issue_key, project, source)
            seen.append(run_id)
            return TriageSyncResult(
                outcome=TriageRecommendation(
                    recommended_issue_type="Story",
                    recommended_priority=None,
                    confidence=0.5,
                    reason="capture stub",
                ),
            )

    app_client = TestClient(create_app(triage_handler_factory=lambda: _CapturingRunner()))
    payload = {"issue_key": "TJC-9", "project": "TJC", "source": "bug_created"}
    response = app_client.post("/triage", json=payload, headers=_auth_headers())
    assert response.status_code == 200
    data = response.json()
    assert len(seen) == 1
    assert data["run_id"] == seen[0]


@pytest.mark.unit
def test_post_triage_calls_flush_inference_telemetry_when_runner_exposes_it() -> None:
    flush_calls = 0

    class _RunnerWithFlush:
        def run_sync(
            self,
            issue_key: str,
            project: str,
            source: str,
            *,
            run_id: str,
        ) -> TriageSyncResult:
            _ = (issue_key, project, source, run_id)
            return TriageSyncResult(
                outcome=TriageRecommendation(
                    recommended_issue_type="Story",
                    recommended_priority=None,
                    confidence=0.5,
                    reason="flush stub",
                ),
            )

        def flush_inference_telemetry(self) -> None:
            nonlocal flush_calls
            flush_calls += 1

    app_client = TestClient(create_app(triage_handler_factory=lambda: _RunnerWithFlush()))
    payload = {"issue_key": "TJC-9", "project": "TJC", "source": "bug_created"}
    response = app_client.post("/triage", json=payload, headers=_auth_headers())
    assert response.status_code == 200
    assert flush_calls == 1


@pytest.mark.unit
def test_post_triage_accepts_manual_cli_source(client: TestClient) -> None:
    payload = {"issue_key": "TJC-9", "project": "TJC", "source": "manual_trigger"}
    response = client.post("/triage", json=payload, headers=_auth_headers())
    assert response.status_code == 200
    data = response.json()
    assert data["source"] == "manual_trigger"
    assert data["status"] == "completed"


@pytest.mark.unit
def test_post_triage_accepts_bug_created_source(client: TestClient) -> None:
    payload = {"issue_key": "TJC-42", "project": "TJC", "source": "bug_created"}
    response = client.post("/triage", json=payload, headers=_auth_headers())
    assert response.status_code == 200
    data = response.json()
    assert data["issue_key"] == "TJC-42"
    assert data["project"] == "TJC"
    assert data["source"] == "bug_created"
    assert data["status"] == "completed"
    assert data["failure"] is None
    assert data["recommendation"]["recommended_issue_type"] == "Story"
    assert data["recommendation"]["recommended_priority"] is None


@pytest.mark.unit
def test_post_triage_accepts_priority_changed_source(client: TestClient) -> None:
    payload = {"issue_key": "TJC-42", "project": "TJC", "source": "priority_changed"}
    response = client.post("/triage", json=payload, headers=_auth_headers())
    assert response.status_code == 200
    data = response.json()
    assert data["source"] == "priority_changed"
    assert data["status"] == "completed"


@pytest.mark.unit
def test_post_triage_returns_422_when_issue_key_missing(client: TestClient) -> None:
    response = client.post(
        "/triage",
        json={"project": "TJC", "source": "bug_created"},
        headers=_auth_headers(),
    )
    assert response.status_code == 422


@pytest.mark.unit
def test_post_triage_returns_422_when_project_missing(client: TestClient) -> None:
    response = client.post(
        "/triage",
        json={"issue_key": "TJC-1", "source": "bug_created"},
        headers=_auth_headers(),
    )
    assert response.status_code == 422


@pytest.mark.unit
def test_post_triage_returns_422_when_source_missing(client: TestClient) -> None:
    response = client.post(
        "/triage",
        json={"issue_key": "TJC-1", "project": "TJC"},
        headers=_auth_headers(),
    )
    assert response.status_code == 422


@pytest.mark.unit
def test_post_triage_returns_422_when_source_not_supported(client: TestClient) -> None:
    response = client.post(
        "/triage",
        json={"issue_key": "TJC-1", "project": "TJC", "source": "scheduled_scan"},
        headers=_auth_headers(),
    )
    assert response.status_code == 422


@pytest.mark.unit
def test_post_triage_returns_422_when_source_is_priority_change_typo(client: TestClient) -> None:
    """Jira payloads sometimes use ``priority_change``; the API enum is ``priority_changed``."""
    response = client.post(
        "/triage",
        json={"issue_key": "TJC-1", "project": "TJC", "source": "priority_change"},
        headers=_auth_headers(),
    )
    assert response.status_code == 422


@pytest.mark.unit
def test_post_triage_returns_422_when_issue_key_empty_string(client: TestClient) -> None:
    response = client.post(
        "/triage",
        json={"issue_key": "", "project": "TJC", "source": "bug_created"},
        headers=_auth_headers(),
    )
    assert response.status_code == 422


@pytest.mark.unit
def test_post_triage_returns_422_when_project_empty_string(client: TestClient) -> None:
    response = client.post(
        "/triage",
        json={"issue_key": "TJC-1", "project": "", "source": "bug_created"},
        headers=_auth_headers(),
    )
    assert response.status_code == 422


@pytest.mark.unit
def test_post_triage_returns_422_when_source_empty_string(client: TestClient) -> None:
    response = client.post(
        "/triage",
        json={"issue_key": "TJC-1", "project": "TJC", "source": ""},
        headers=_auth_headers(),
    )
    assert response.status_code == 422


@pytest.mark.unit
def test_post_triage_returns_failed_status_when_runner_returns_triage_failure() -> None:
    class _FailingRunner:
        def run_sync(
            self,
            issue_key: str,
            project: str,
            source: str,
            *,
            run_id: str,
        ) -> TriageSyncResult:
            _ = (issue_key, project, source, run_id)
            return TriageSyncResult(outcome=fallback_for_exception(RuntimeError("boom")))

    app_client = TestClient(create_app(triage_handler_factory=lambda: _FailingRunner()))
    response = app_client.post(
        "/triage",
        json={"issue_key": "TJC-1", "project": "TJC", "source": "bug_created"},
        headers=_auth_headers(),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "failed"
    assert data["recommendation"] is None
    assert data["failure"]["category"] == "internal_error"
    assert "boom" in data["failure"]["message"]
    uuid.UUID(data["run_id"])


@pytest.mark.unit
def test_stub_runner_satisfies_triage_runner_protocol() -> None:
    runner: TriageRunner = _StubRunner()
    result = runner.run_sync("k", "p", "bug_created", run_id=str(uuid.uuid4()))
    assert isinstance(result, TriageSyncResult)
    assert isinstance(result.outcome, TriageRecommendation)
