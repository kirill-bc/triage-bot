"""Unit tests for LangFuse-backed triage audit event sink."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from triage_service.observability.audit_events import ClassificationCompletedAuditEvent


def _sample_event() -> ClassificationCompletedAuditEvent:
    return ClassificationCompletedAuditEvent(
        event_type="classification_completed",
        run_id="550e8400-e29b-41d4-a716-446655440000",
        issue_key="TJC-12",
        project="TJC",
        source="manual_cli",
        recommended_issue_type="Bug",
        confidence=0.87,
        reason="Contains production outage signals.",
    )


@pytest.mark.unit
def test_langfuse_audit_store_record_noop_without_client() -> None:
    from triage_service.observability.langfuse_audit_store import LangfuseAuditStore

    store = LangfuseAuditStore(client=None)
    store.record(_sample_event())


@pytest.mark.unit
def test_langfuse_audit_store_records_event_with_trace_id_and_metadata() -> None:
    from triage_service.observability.langfuse_audit_store import LangfuseAuditStore

    client = MagicMock()
    store = LangfuseAuditStore(client=client)
    event = _sample_event()

    store.record(event)

    client.create_event.assert_called_once_with(
        name="classification_completed",
        trace_id="550e8400e29b41d4a716446655440000",
        metadata={
            "event_type": "classification_completed",
            "run_id": "550e8400-e29b-41d4-a716-446655440000",
            "issue_key": "TJC-12",
            "project": "TJC",
            "source": "manual_cli",
            "recommended_issue_type": "Bug",
            "confidence": 0.87,
            "reason": "Contains production outage signals.",
        },
    )


@pytest.mark.unit
def test_langfuse_audit_store_swallows_client_errors() -> None:
    from triage_service.observability.langfuse_audit_store import LangfuseAuditStore

    client = MagicMock()
    client.create_event.side_effect = RuntimeError("langfuse down")
    store = LangfuseAuditStore(client=client)

    store.record(_sample_event())


@pytest.mark.unit
def test_build_langfuse_audit_store_noop_without_keys() -> None:
    from triage_service.observability.langfuse_audit_store import build_langfuse_audit_store

    store = build_langfuse_audit_store(public_key=None, secret_key=None)
    assert store._client is None
    partial = build_langfuse_audit_store(public_key="pk", secret_key=" ")
    assert partial._client is None


@pytest.mark.unit
def test_build_langfuse_audit_store_passes_keys_to_client() -> None:
    from triage_service.observability.langfuse_audit_store import build_langfuse_audit_store

    with patch("triage_service.observability.langfuse_audit_store.Langfuse") as mocked:
        build_langfuse_audit_store(
            public_key="pk-lf",
            secret_key="sk-lf",
            base_url="https://example.invalid",
        )

    mocked.assert_called_once_with(
        public_key="pk-lf",
        secret_key="sk-lf",
        base_url="https://example.invalid",
    )
