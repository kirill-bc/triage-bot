"""Optional raw-body logging for POST /triage (TRIAGE_DEBUG_INBOUND)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from triage_api import (
    create_app,
    preview_request_body_for_log,
    triage_inbound_debug_enabled,
)
from triage_fallback import TriageFailure
from triage_recommendation_parser import TriageRecommendation


@pytest.mark.unit
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("", False),
        ("0", False),
        ("no", False),
        ("1", True),
        ("true", True),
        ("TRUE", True),
        (" yes ", True),
        ("on", True),
    ],
)
def test_triage_inbound_debug_enabled(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
    expected: bool,
) -> None:
    monkeypatch.delenv("TRIAGE_DEBUG_INBOUND", raising=False)
    if value != "":
        monkeypatch.setenv("TRIAGE_DEBUG_INBOUND", value)
    assert triage_inbound_debug_enabled() is expected


@pytest.mark.unit
def test_preview_request_body_truncates_long_payload() -> None:
    body = b"x" * 9000
    text = preview_request_body_for_log(body, max_len=100)
    assert "truncated" in text
    assert len(text) < len(body)


class _StubRunner:
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
            reason="stub",
        )


@pytest.mark.unit
def test_debug_inbound_logs_raw_body_when_validation_fails(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("TRIAGE_DEBUG_INBOUND", "1")
    client = TestClient(create_app(triage_handler_factory=lambda: _StubRunner()))
    response = client.post("/triage", json={"issues": []})
    assert response.status_code == 422
    err = capsys.readouterr().err
    assert "[TRIAGE_DEBUG_INBOUND]" in err
    assert "issues" in err


@pytest.mark.unit
def test_debug_inbound_does_not_log_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("TRIAGE_DEBUG_INBOUND", raising=False)
    client = TestClient(create_app(triage_handler_factory=lambda: _StubRunner()))
    response = client.post("/triage", json={"issues": []})
    assert response.status_code == 422
    assert "[TRIAGE_DEBUG_INBOUND]" not in capsys.readouterr().err


@pytest.mark.unit
def test_debug_inbound_valid_request_still_succeeds(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("TRIAGE_DEBUG_INBOUND", "1")
    client = TestClient(create_app(triage_handler_factory=lambda: _StubRunner()))
    response = client.post(
        "/triage",
        json={"issue_key": "TJC-1", "project": "TJC", "source": "bug_created"},
    )
    assert response.status_code == 200
    err = capsys.readouterr().err
    assert "[TRIAGE_DEBUG_INBOUND]" in err
    assert "TJC-1" in err
