"""POST /triage request and response contract.

The webhook is fired by a Jira Automation **scheduled** rule (JQL-driven scan),
not by an event trigger. The request body carries a ``source`` annotation
(``scheduled_scan`` for MVP) rather than a Jira event type; this leaves room
for ``manual_cli`` / future sources without changing the contract shape.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from triage_api import create_app


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


@pytest.mark.unit
def test_post_triage_accepts_issue_key_project_and_source(client: TestClient) -> None:
    payload = {"issue_key": "TJC-42", "project": "TJC", "source": "scheduled_scan"}
    response = client.post("/triage", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["issue_key"] == "TJC-42"
    assert data["project"] == "TJC"
    assert data["source"] == "scheduled_scan"


@pytest.mark.unit
def test_post_triage_returns_422_when_issue_key_missing(client: TestClient) -> None:
    response = client.post(
        "/triage",
        json={"project": "TJC", "source": "scheduled_scan"},
    )
    assert response.status_code == 422


@pytest.mark.unit
def test_post_triage_returns_422_when_project_missing(client: TestClient) -> None:
    response = client.post(
        "/triage",
        json={"issue_key": "TJC-1", "source": "scheduled_scan"},
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
        json={"issue_key": "TJC-1", "project": "TJC", "source": "issue_created"},
    )
    assert response.status_code == 422


@pytest.mark.unit
def test_post_triage_returns_422_when_issue_key_empty_string(client: TestClient) -> None:
    response = client.post(
        "/triage",
        json={"issue_key": "", "project": "TJC", "source": "scheduled_scan"},
    )
    assert response.status_code == 422


@pytest.mark.unit
def test_post_triage_returns_422_when_project_empty_string(client: TestClient) -> None:
    response = client.post(
        "/triage",
        json={"issue_key": "TJC-1", "project": "", "source": "scheduled_scan"},
    )
    assert response.status_code == 422


@pytest.mark.unit
def test_post_triage_returns_422_when_source_empty_string(client: TestClient) -> None:
    response = client.post(
        "/triage",
        json={"issue_key": "TJC-1", "project": "TJC", "source": ""},
    )
    assert response.status_code == 422
