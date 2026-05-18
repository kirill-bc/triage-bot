"""GET /health liveness and minimal readiness (validated settings load)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from triage_service.api.triage_api import create_app
from triage_service.core.triage_recommendation_parser import TriageRecommendation


class _StubRunner:
    def run_sync(
        self,
        issue_key: str,
        project: str,
        source: str,
        *,
        run_id: str,
    ) -> TriageRecommendation:
        _ = (issue_key, project, source, run_id)
        return TriageRecommendation(
            recommended_issue_type="Story",
            recommended_priority=None,
            confidence=0.5,
            reason="health test stub",
        )


@pytest.mark.unit
def test_get_health_returns_200_and_ready_true_when_settings_load(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JIRA_API_KEY", "jira-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-token")
    monkeypatch.setenv("TRIAGE_WEBHOOK_TOKEN", "triage-token")
    client = TestClient(create_app(triage_handler_factory=lambda: _StubRunner()))
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data.get("service") == "jira-triage"
    assert data.get("ready") is True
    obs = data.get("observability")
    assert isinstance(obs, dict)
    assert obs.get("langfuse_inference_enabled") is False
    assert obs.get("langfuse_public_key_present") is False
    assert obs.get("langfuse_secret_key_present") is False
    assert obs.get("langfuse_sdk_tracing_env_enabled") is True
    assert obs.get("otel_sdk_disabled") is False
    assert obs.get("langfuse_export_env_ready") is False
    assert obs.get("audit_langfuse_enabled") is True
    assert obs.get("langfuse_audit_sink_enabled") is False
    assert obs.get("audit_structured_log_enabled") is True


@pytest.mark.unit
def test_get_health_returns_503_and_ready_false_when_settings_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("JIRA_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("TRIAGE_WEBHOOK_TOKEN", raising=False)
    client = TestClient(create_app(triage_handler_factory=lambda: _StubRunner()))
    response = client.get("/health")
    assert response.status_code == 503
    data = response.json()
    assert data.get("service") == "jira-triage"
    assert data.get("ready") is False
    assert "observability" not in data


@pytest.mark.unit
def test_get_health_includes_observability_langfuse_enabled_when_keys_set(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JIRA_API_KEY", "jira-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-token")
    monkeypatch.setenv("TRIAGE_WEBHOOK_TOKEN", "triage-token")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    client = TestClient(create_app(triage_handler_factory=lambda: _StubRunner()))
    response = client.get("/health")
    assert response.status_code == 200
    obs = response.json().get("observability")
    assert isinstance(obs, dict)
    assert obs.get("langfuse_inference_enabled") is True
    assert obs.get("langfuse_public_key_present") is True
    assert obs.get("langfuse_secret_key_present") is True
    assert obs.get("langfuse_sdk_tracing_env_enabled") is True
    assert obs.get("otel_sdk_disabled") is False
    assert obs.get("langfuse_export_env_ready") is True
    assert obs.get("langfuse_audit_sink_enabled") is True


@pytest.mark.unit
def test_get_health_observability_langfuse_export_not_ready_when_tracing_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JIRA_API_KEY", "jira-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-token")
    monkeypatch.setenv("TRIAGE_WEBHOOK_TOKEN", "triage-token")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    monkeypatch.setenv("LANGFUSE_TRACING_ENABLED", "false")
    client = TestClient(create_app(triage_handler_factory=lambda: _StubRunner()))
    obs = client.get("/health").json().get("observability")
    assert isinstance(obs, dict)
    assert obs.get("langfuse_inference_enabled") is True
    assert obs.get("langfuse_sdk_tracing_env_enabled") is False
    assert obs.get("langfuse_export_env_ready") is False


@pytest.mark.unit
def test_get_health_observability_langfuse_export_not_ready_when_otel_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JIRA_API_KEY", "jira-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-token")
    monkeypatch.setenv("TRIAGE_WEBHOOK_TOKEN", "triage-token")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    client = TestClient(create_app(triage_handler_factory=lambda: _StubRunner()))
    obs = client.get("/health").json().get("observability")
    assert isinstance(obs, dict)
    assert obs.get("otel_sdk_disabled") is True
    assert obs.get("langfuse_export_env_ready") is False


@pytest.mark.unit
def test_get_health_does_not_instantiate_triage_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Readiness must not depend on the injected triage handler factory."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JIRA_API_KEY", "jira-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-token")
    monkeypatch.setenv("TRIAGE_WEBHOOK_TOKEN", "triage-token")

    def _should_not_call() -> _StubRunner:
        raise AssertionError("triage runner factory must not run for GET /health")

    client = TestClient(create_app(triage_handler_factory=_should_not_call))
    response = client.get("/health")
    assert response.status_code == 200
