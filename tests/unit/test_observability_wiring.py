"""Unit tests for default observability bundle composition."""

from __future__ import annotations

import pytest

from triage_service.core.settings import AppSettings
from triage_service.observability.audit_store import CompositeAuditStore
from triage_service.observability.langfuse_audit_store import LangfuseAuditStore
from triage_service.observability.observability_wiring import (
    NoOpAuditStore,
    build_triage_observability,
)
from triage_service.observability.structured_logger_audit_store import StructuredLoggerAuditStore


def _minimal_settings(monkeypatch: pytest.MonkeyPatch) -> AppSettings:
    monkeypatch.setenv("JIRA_API_KEY", "jira-api-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-token")
    return AppSettings()


@pytest.mark.unit
def test_build_triage_observability_structured_only_when_langfuse_keys_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _minimal_settings(monkeypatch)
    obs = build_triage_observability(settings)
    assert obs.inference_tracer._client is None
    assert isinstance(obs.audit_store, StructuredLoggerAuditStore)


@pytest.mark.unit
def test_build_triage_observability_composite_when_langfuse_keys_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JIRA_API_KEY", "jira-api-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-token")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    settings = AppSettings()
    obs = build_triage_observability(settings)
    assert obs.inference_tracer._client is not None
    assert isinstance(obs.audit_store, CompositeAuditStore)
    kinds = [type(s).__name__ for s in obs.audit_store._stores]  # noqa: SLF001
    assert "StructuredLoggerAuditStore" in kinds
    assert "LangfuseAuditStore" in kinds


@pytest.mark.unit
def test_build_triage_observability_langfuse_audit_only_when_structured_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JIRA_API_KEY", "jira-api-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-token")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    monkeypatch.setenv("TRIAGE_AUDIT_STRUCTURED_LOG_ENABLED", "false")
    settings = AppSettings()
    obs = build_triage_observability(settings)
    assert obs.inference_tracer._client is not None
    assert isinstance(obs.audit_store, LangfuseAuditStore)
    assert obs.audit_store._client is not None


@pytest.mark.unit
def test_build_triage_observability_skips_langfuse_audit_when_flag_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JIRA_API_KEY", "jira-api-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-token")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    monkeypatch.setenv("TRIAGE_AUDIT_LANGFUSE_ENABLED", "false")
    settings = AppSettings()
    obs = build_triage_observability(settings)
    assert obs.inference_tracer._client is not None
    assert isinstance(obs.audit_store, StructuredLoggerAuditStore)


@pytest.mark.unit
def test_build_triage_observability_noop_audit_when_both_sinks_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JIRA_API_KEY", "jira-api-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-token")
    monkeypatch.setenv("TRIAGE_AUDIT_STRUCTURED_LOG_ENABLED", "false")
    monkeypatch.setenv("TRIAGE_AUDIT_LANGFUSE_ENABLED", "false")
    settings = AppSettings()
    obs = build_triage_observability(settings)

    assert isinstance(obs.audit_store, NoOpAuditStore)
