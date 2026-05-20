"""Unit tests for default observability bundle composition."""

from __future__ import annotations

import logging

import pytest

from triage_service.core.settings import AppSettings
from triage_service.observability.audit_store import CompositeAuditStore
from triage_service.observability.langfuse_audit_store import LangfuseAuditStore
from triage_service.observability.observability_wiring import (
    NoOpAuditStore,
    build_triage_observability,
    observability_status_summary,
)
from triage_service.observability.structured_logger_audit_store import StructuredLoggerAuditStore


def _minimal_settings(monkeypatch: pytest.MonkeyPatch) -> AppSettings:
    monkeypatch.setenv("JIRA_API_KEY", "jira-api-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-token")
    monkeypatch.setenv("TRIAGE_WEBHOOK_TOKEN", "triage-token")
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_BASE_URL", raising=False)
    return AppSettings()


@pytest.mark.unit
def test_observability_status_summary_reflects_langfuse_key_presence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _minimal_settings(monkeypatch)
    s0 = observability_status_summary(settings)
    assert s0["langfuse_inference_enabled"] is False
    assert s0["langfuse_audit_sink_enabled"] is False

    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://cloud.langfuse.com")
    s1 = observability_status_summary(AppSettings())
    assert s1["langfuse_inference_enabled"] is True
    assert s1["langfuse_base_url_configured"] is True
    assert s1["langfuse_audit_sink_enabled"] is True

    monkeypatch.setenv("TRIAGE_AUDIT_LANGFUSE_ENABLED", "false")
    s2 = observability_status_summary(AppSettings())
    assert s2["langfuse_inference_enabled"] is True
    assert s2["langfuse_audit_sink_enabled"] is False

    monkeypatch.setenv("LANGFUSE_TRACING_ENABLED", "false")
    s3 = observability_status_summary(AppSettings())
    assert s3["langfuse_sdk_tracing_env_enabled"] is False
    assert s3["langfuse_export_env_ready"] is False

    monkeypatch.delenv("LANGFUSE_TRACING_ENABLED", raising=False)
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    s4 = observability_status_summary(AppSettings())
    assert s4["otel_sdk_disabled"] is True
    assert s4["langfuse_export_env_ready"] is False


@pytest.mark.unit
def test_build_triage_observability_structured_only_when_langfuse_keys_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _minimal_settings(monkeypatch)
    obs = build_triage_observability(settings)
    assert obs.inference_tracer._client is None
    assert isinstance(obs.audit_store, StructuredLoggerAuditStore)


@pytest.mark.unit
def test_build_triage_observability_wires_vision_transcript_redaction_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JIRA_API_KEY", "jira-api-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-token")
    monkeypatch.setenv("TRIAGE_WEBHOOK_TOKEN", "triage-token")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    monkeypatch.setenv("TRIAGE_AUDIT_REDACT_IMAGE_TRANSCRIPT", "false")
    settings = AppSettings()
    obs = build_triage_observability(settings)
    assert obs.inference_tracer._redact_vision_transcript is False


@pytest.mark.unit
def test_build_triage_observability_composite_when_langfuse_keys_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JIRA_API_KEY", "jira-api-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-token")
    monkeypatch.setenv("TRIAGE_WEBHOOK_TOKEN", "triage-token")
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
    monkeypatch.setenv("TRIAGE_WEBHOOK_TOKEN", "triage-token")
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
    monkeypatch.setenv("TRIAGE_WEBHOOK_TOKEN", "triage-token")
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
    monkeypatch.setenv("TRIAGE_WEBHOOK_TOKEN", "triage-token")
    monkeypatch.setenv("TRIAGE_AUDIT_STRUCTURED_LOG_ENABLED", "false")
    monkeypatch.setenv("TRIAGE_AUDIT_LANGFUSE_ENABLED", "false")
    settings = AppSettings()
    obs = build_triage_observability(settings)

    assert isinstance(obs.audit_store, NoOpAuditStore)


@pytest.mark.unit
def test_build_triage_observability_logs_langfuse_status_when_keys_missing(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(
        logging.INFO,
        logger="triage_service.observability.observability_wiring",
    )
    settings = _minimal_settings(monkeypatch)
    _ = build_triage_observability(settings)

    matching = [
        r
        for r in caplog.records
        if getattr(r, "event_type", "") == "triage_observability_config"
    ]
    assert matching
    rec = matching[-1]
    assert bool(getattr(rec, "langfuse_inference_enabled", True)) is False
    assert bool(getattr(rec, "langfuse_public_key_present", True)) is False
    assert bool(getattr(rec, "langfuse_secret_key_present", True)) is False
    assert bool(getattr(rec, "audit_langfuse_enabled", False)) is True


@pytest.mark.unit
def test_build_triage_observability_logs_langfuse_status_when_keys_present(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(
        logging.INFO,
        logger="triage_service.observability.observability_wiring",
    )
    monkeypatch.setenv("JIRA_API_KEY", "jira-api-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-token")
    monkeypatch.setenv("TRIAGE_WEBHOOK_TOKEN", "triage-token")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    settings = AppSettings()
    _ = build_triage_observability(settings)

    matching = [
        r
        for r in caplog.records
        if getattr(r, "event_type", "") == "triage_observability_config"
    ]
    assert matching
    rec = matching[-1]
    assert bool(getattr(rec, "langfuse_inference_enabled", False)) is True
    assert bool(getattr(rec, "langfuse_public_key_present", False)) is True
    assert bool(getattr(rec, "langfuse_secret_key_present", False)) is True
    assert bool(getattr(rec, "langfuse_audit_sink_enabled", False)) is True
    assert bool(getattr(rec, "langfuse_runtime_tracing_enabled", False)) is True


@pytest.mark.unit
def test_build_triage_observability_logs_runtime_tracing_false_when_sdk_disabled(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(
        logging.INFO,
        logger="triage_service.observability.observability_wiring",
    )
    monkeypatch.setenv("JIRA_API_KEY", "jira-api-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-token")
    monkeypatch.setenv("TRIAGE_WEBHOOK_TOKEN", "triage-token")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    monkeypatch.setenv("LANGFUSE_TRACING_ENABLED", "false")
    settings = AppSettings()
    _ = build_triage_observability(settings)

    matching = [
        r
        for r in caplog.records
        if getattr(r, "event_type", "") == "triage_observability_config"
    ]
    assert matching
    rec = matching[-1]
    assert bool(getattr(rec, "langfuse_runtime_tracing_enabled", True)) is False
