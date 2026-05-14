"""Unit tests for structured JSON logger-backed triage audit sink."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from triage_service.observability.audit_events import ClassificationCompletedAuditEvent


def _sample_event() -> ClassificationCompletedAuditEvent:
    return ClassificationCompletedAuditEvent(
        event_type="classification_completed",
        run_id="550e8400-e29b-41d4-a716-446655440000",
        issue_key="TJC-17",
        project="TJC",
        source="manual_cli",
        recommended_issue_type="Bug",
        confidence=0.78,
        reason="Regression-like behavior in production steps.",
    )


@pytest.mark.unit
def test_structured_logger_audit_store_records_json_payload() -> None:
    from triage_service.observability.structured_logger_audit_store import (
        StructuredLoggerAuditStore,
    )

    logger = MagicMock()
    store = StructuredLoggerAuditStore(logger=logger)

    store.record(_sample_event())

    logger.info.assert_called_once()
    payload_text = logger.info.call_args.args[0]
    payload = json.loads(payload_text)
    assert payload == {
        "event_type": "classification_completed",
        "run_id": "550e8400-e29b-41d4-a716-446655440000",
        "issue_key": "TJC-17",
        "project": "TJC",
        "source": "manual_cli",
        "recommended_issue_type": "Bug",
        "confidence": 0.78,
        "reason": "Regression-like behavior in production steps.",
    }


@pytest.mark.unit
def test_structured_logger_audit_store_truncates_oversized_strings() -> None:
    from triage_service.observability.structured_logger_audit_store import (
        StructuredLoggerAuditStore,
    )

    long_reason = "R" * 200
    event = ClassificationCompletedAuditEvent(
        event_type="classification_completed",
        run_id="550e8400-e29b-41d4-a716-446655440000",
        issue_key="TJC-17",
        project="TJC",
        source="manual_cli",
        recommended_issue_type="Bug",
        confidence=0.78,
        reason=long_reason,
    )
    logger = MagicMock()
    store = StructuredLoggerAuditStore(logger=logger, max_audit_string_chars=80)

    store.record(event)

    payload = json.loads(logger.info.call_args.args[0])
    assert payload["log_payload_truncated"] is True
    assert len(payload["reason"]) < len(long_reason)
    assert "truncated" in payload["reason"]
    from triage_service.observability.structured_logger_audit_store import (
        StructuredLoggerAuditStore,
    )

    logger = MagicMock()
    logger.info.side_effect = RuntimeError("logger down")
    store = StructuredLoggerAuditStore(logger=logger)

    store.record(_sample_event())
