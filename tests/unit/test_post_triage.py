"""POST /triage request and response contract."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from triage_api import create_app


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


@pytest.mark.unit
def test_post_triage_accepts_issue_key_project_and_event_type(client: TestClient) -> None:
    payload = {"issue_key": "TJC-42", "project": "TJC", "event_type": "issue_updated"}
    response = client.post("/triage", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["issue_key"] == "TJC-42"
    assert data["project"] == "TJC"
    assert data["event_type"] == "issue_updated"


@pytest.mark.unit
def test_post_triage_returns_422_when_issue_key_missing(client: TestClient) -> None:
    response = client.post(
        "/triage",
        json={"project": "TJC", "event_type": "issue_updated"},
    )
    assert response.status_code == 422


@pytest.mark.unit
def test_post_triage_returns_422_when_project_missing(client: TestClient) -> None:
    response = client.post(
        "/triage",
        json={"issue_key": "TJC-1", "event_type": "issue_updated"},
    )
    assert response.status_code == 422


@pytest.mark.unit
def test_post_triage_returns_422_when_event_type_missing(client: TestClient) -> None:
    response = client.post(
        "/triage",
        json={"issue_key": "TJC-1", "project": "TJC"},
    )
    assert response.status_code == 422
