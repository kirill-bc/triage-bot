"""POST /triage request and response contract.

The request body carries a ``source`` annotation: ``bug_created`` or
``priority_changed`` for Jira Automation triggers, or ``manual_cli`` for the
local runner.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from triage_service.api.triage_api import create_app
from triage_service.core.triage_fallback import TriageFailure, fallback_for_exception
from triage_service.core.triage_handler import TriageRunner
from triage_service.core.triage_recommendation_parser import TriageRecommendation


class _StubRunner:
    """Returns a fixed recommendation without touching Jira or OpenRouter."""

    def run_sync(
        self,
        issue_key: str,
        project: str,
        source: str,
    ) -> TriageRecommendation | TriageFailure:
        return TriageRecommendation(
            recommended_issue_type="Story",
            recommended_priority=None,
            confidence=0.5,
            reason="acceptance stub",
        )


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(triage_handler_factory=lambda: _StubRunner()))


@pytest.mark.unit
def test_post_triage_accepts_manual_cli_source(client: TestClient) -> None:
    payload = {"issue_key": "TJC-9", "project": "TJC", "source": "manual_cli"}
    response = client.post("/triage", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["source"] == "manual_cli"
    assert data["status"] == "completed"


@pytest.mark.unit
def test_post_triage_accepts_bug_created_source(client: TestClient) -> None:
    payload = {"issue_key": "TJC-42", "project": "TJC", "source": "bug_created"}
    response = client.post("/triage", json=payload)
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
    response = client.post("/triage", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["source"] == "priority_changed"
    assert data["status"] == "completed"


@pytest.mark.unit
def test_post_triage_returns_422_when_issue_key_missing(client: TestClient) -> None:
    response = client.post(
        "/triage",
        json={"project": "TJC", "source": "bug_created"},
    )
    assert response.status_code == 422


@pytest.mark.unit
def test_post_triage_returns_422_when_project_missing(client: TestClient) -> None:
    response = client.post(
        "/triage",
        json={"issue_key": "TJC-1", "source": "bug_created"},
    )
    assert response.status_code == 422


@pytest.mark.unit
def test_post_triage_returns_422_when_source_missing(client: TestClient) -> None:
    response = client.post(
        "/triage",
        json={"issue_key": "TJC-1", "project": "TJC"},
    )
    assert response.status_code == 422


@pytest.mark.unit
def test_post_triage_returns_422_when_source_not_supported(client: TestClient) -> None:
    response = client.post(
        "/triage",
        json={"issue_key": "TJC-1", "project": "TJC", "source": "scheduled_scan"},
    )
    assert response.status_code == 422


@pytest.mark.unit
def test_post_triage_returns_422_when_source_is_priority_change_typo(client: TestClient) -> None:
    """Jira payloads sometimes use ``priority_change``; the API enum is ``priority_changed``."""
    response = client.post(
        "/triage",
        json={"issue_key": "TJC-1", "project": "TJC", "source": "priority_change"},
    )
    assert response.status_code == 422


@pytest.mark.unit
def test_post_triage_returns_422_when_issue_key_empty_string(client: TestClient) -> None:
    response = client.post(
        "/triage",
        json={"issue_key": "", "project": "TJC", "source": "bug_created"},
    )
    assert response.status_code == 422


@pytest.mark.unit
def test_post_triage_returns_422_when_project_empty_string(client: TestClient) -> None:
    response = client.post(
        "/triage",
        json={"issue_key": "TJC-1", "project": "", "source": "bug_created"},
    )
    assert response.status_code == 422


@pytest.mark.unit
def test_post_triage_returns_422_when_source_empty_string(client: TestClient) -> None:
    response = client.post(
        "/triage",
        json={"issue_key": "TJC-1", "project": "TJC", "source": ""},
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
        ) -> TriageRecommendation | TriageFailure:
            return fallback_for_exception(RuntimeError("boom"))

    app_client = TestClient(create_app(triage_handler_factory=lambda: _FailingRunner()))
    response = app_client.post(
        "/triage",
        json={"issue_key": "TJC-1", "project": "TJC", "source": "bug_created"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "failed"
    assert data["recommendation"] is None
    assert data["failure"]["category"] == "internal_error"
    assert "boom" in data["failure"]["message"]


@pytest.mark.unit
def test_stub_runner_satisfies_triage_runner_protocol() -> None:
    runner: TriageRunner = _StubRunner()
    assert isinstance(runner.run_sync("k", "p", "bug_created"), TriageRecommendation)
