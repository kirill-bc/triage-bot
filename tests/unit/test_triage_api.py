"""Concurrency bounds for POST /triage: semaphore cap, 503 shed, health responsiveness."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from triage_service.api.triage_api import (
    _concurrency_limits_from_settings,
    create_app,
)
from triage_service.core.triage_handler import TriageSyncResult
from triage_service.core.triage_recommendation_parser import TriageRecommendation

_TRIAGE_TOKEN = "test-triage-token"


def _auth_headers() -> dict[str, str]:
    return {"X-Triage-Token": _TRIAGE_TOKEN}


def _stub_outcome() -> TriageRecommendation:
    return TriageRecommendation(
        recommended_issue_type="Story",
        recommended_priority=None,
        confidence=0.5,
        reason="concurrency test stub",
    )


class _BlockingRunner:
    """Tracks concurrent in-flight run_sync calls and holds the slot for ``hold_seconds``."""

    def __init__(self, hold_seconds: float) -> None:
        self._hold_seconds = hold_seconds
        self._lock = threading.Lock()
        self._current = 0
        self.peak = 0

    def run_sync(
        self,
        issue_key: str,
        project: str,
        source: str,
        *,
        run_id: str,
    ) -> TriageSyncResult:
        _ = (issue_key, project, source, run_id)
        with self._lock:
            self._current += 1
            self.peak = max(self.peak, self._current)
        time.sleep(self._hold_seconds)
        with self._lock:
            self._current -= 1
        return TriageSyncResult(outcome=_stub_outcome())


@pytest.fixture(autouse=True)
def _configure_triage_webhook_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRIAGE_WEBHOOK_TOKEN", _TRIAGE_TOKEN)


def _required_settings_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JIRA_API_KEY", "jira-api-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-token")
    monkeypatch.setenv("TRIAGE_WEBHOOK_TOKEN", _TRIAGE_TOKEN)


@pytest.mark.unit
def test_concurrency_limits_from_settings_use_defaults_when_values_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _required_settings_env(monkeypatch)
    monkeypatch.setenv("TRIAGE_MAX_CONCURRENT_RUNS", "1000")
    assert _concurrency_limits_from_settings() == (4, 900.0)
    monkeypatch.setenv("TRIAGE_MAX_CONCURRENT_RUNS", "0")
    assert _concurrency_limits_from_settings() == (4, 900.0)
    monkeypatch.setenv("TRIAGE_MAX_CONCURRENT_RUNS", "64")
    monkeypatch.setenv("TRIAGE_CONCURRENCY_WAIT_SECONDS", "3600")
    assert _concurrency_limits_from_settings() == (64, 3600.0)


@pytest.mark.unit
def test_concurrency_wait_seconds_use_defaults_when_values_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _required_settings_env(monkeypatch)
    monkeypatch.setenv("TRIAGE_CONCURRENCY_WAIT_SECONDS", "9999")
    assert _concurrency_limits_from_settings()[1] == 900.0
    monkeypatch.setenv("TRIAGE_CONCURRENCY_WAIT_SECONDS", "0")
    assert _concurrency_limits_from_settings()[1] == 900.0
    monkeypatch.setenv("TRIAGE_CONCURRENCY_WAIT_SECONDS", "3600")
    assert _concurrency_limits_from_settings()[1] == 3600.0


@pytest.mark.unit
def test_post_triage_limits_concurrent_executions_to_configured_max(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRIAGE_MAX_CONCURRENT_RUNS", "2")
    monkeypatch.setenv("TRIAGE_CONCURRENCY_WAIT_SECONDS", "5")
    runner = _BlockingRunner(hold_seconds=0.3)
    client = TestClient(create_app(triage_handler_factory=lambda: runner))
    payload = {"issue_key": "TJC-1", "project": "TJC", "source": "manual_trigger"}

    def _call() -> int:
        return client.post("/triage", json=payload, headers=_auth_headers()).status_code

    with ThreadPoolExecutor(max_workers=6) as pool:
        statuses = list(pool.map(lambda _: _call(), range(6)))

    assert statuses == [200] * 6
    assert runner.peak == 2


@pytest.mark.unit
def test_post_triage_returns_503_when_concurrency_wait_exceeded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRIAGE_MAX_CONCURRENT_RUNS", "1")
    monkeypatch.setenv("TRIAGE_CONCURRENCY_WAIT_SECONDS", "0.3")
    runner = _BlockingRunner(hold_seconds=1.2)
    client = TestClient(create_app(triage_handler_factory=lambda: runner))
    payload = {"issue_key": "TJC-1", "project": "TJC", "source": "manual_trigger"}

    def _call(delay: float) -> int:
        time.sleep(delay)
        return client.post("/triage", json=payload, headers=_auth_headers()).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(_call, 0.0)
        second = pool.submit(_call, 0.05)
        statuses = sorted([first.result(timeout=5), second.result(timeout=5)])

    assert statuses == [200, 503]


@pytest.mark.unit
def test_get_health_responds_promptly_while_triage_slots_saturated(
    tmp_path: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JIRA_API_KEY", "jira-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-token")
    monkeypatch.setenv("TRIAGE_MAX_CONCURRENT_RUNS", "1")
    monkeypatch.setenv("TRIAGE_CONCURRENCY_WAIT_SECONDS", "5")
    runner = _BlockingRunner(hold_seconds=1.5)
    client = TestClient(create_app(triage_handler_factory=lambda: runner))
    payload = {"issue_key": "TJC-1", "project": "TJC", "source": "manual_trigger"}

    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(
            lambda: client.post("/triage", json=payload, headers=_auth_headers()),
        )
        time.sleep(0.2)
        started = time.monotonic()
        response = client.get("/health")
        elapsed = time.monotonic() - started

    assert response.status_code == 200
    assert response.json().get("ready") is True
    assert elapsed < 1.0
