"""Tests for canonical triage lifecycle audit event schemas."""

from __future__ import annotations

import json
from typing import Any, get_args

import pytest
from pydantic import ValidationError

from triage_service.core.triage_fallback import TriageFailureCategory


@pytest.mark.unit
def test_audit_failure_categories_match_triage_failure_category() -> None:
    from triage_service.observability.audit_events import TriageAuditFailureCategory

    assert frozenset(get_args(TriageAuditFailureCategory)) == frozenset(
        get_args(TriageFailureCategory)
    )


@pytest.mark.unit
def test_parse_classification_completed_round_trip() -> None:
    from triage_service.observability.audit_events import (
        dump_triage_audit_event,
        parse_triage_audit_event,
    )

    payload: dict[str, Any] = {
        "event_type": "classification_completed",
        "run_id": "550e8400-e29b-41d4-a716-446655440000",
        "issue_key": "TJC-1",
        "project": "TJC",
        "source": "bug_created",
        "recommended_issue_type": "Bug",
        "confidence": 0.82,
        "reason": "Repro steps indicate defect behavior.",
    }
    event = parse_triage_audit_event(payload)
    dumped = dump_triage_audit_event(event)
    assert dumped == payload
    assert event.event_type == "classification_completed"


@pytest.mark.unit
def test_parse_priority_completed_requires_bug_path_fields() -> None:
    from triage_service.observability.audit_events import parse_triage_audit_event

    payload: dict[str, Any] = {
        "event_type": "priority_completed",
        "run_id": "r1",
        "issue_key": "TJC-2",
        "project": "TJC",
        "source": "manual_trigger",
        "recommended_priority": "P2",
        "confidence": 0.71,
        "reason": "Customer impact moderate.",
    }
    event = parse_triage_audit_event(payload)
    assert event.event_type == "priority_completed"
    assert event.recommended_priority == "P2"


@pytest.mark.unit
def test_parse_triage_completed_story_omits_priority() -> None:
    from triage_service.observability.audit_events import (
        TriageCompletedAuditEvent,
        parse_triage_audit_event,
    )

    payload: dict[str, Any] = {
        "event_type": "triage_completed",
        "run_id": "r2",
        "issue_key": "TJC-3",
        "project": "TJC",
        "source": "priority_changed",
        "recommended_issue_type": "Story",
        "recommended_priority": None,
        "confidence": 0.9,
        "reason": "Feature request phrasing.",
    }
    event = parse_triage_audit_event(payload)
    assert isinstance(event, TriageCompletedAuditEvent)
    assert event.recommended_issue_type == "Story"
    assert event.recommended_priority is None


@pytest.mark.unit
def test_parse_triage_completed_story_rejects_non_null_priority() -> None:
    from triage_service.observability.audit_events import parse_triage_audit_event

    payload: dict[str, Any] = {
        "event_type": "triage_completed",
        "run_id": "r2",
        "issue_key": "TJC-3",
        "project": "TJC",
        "source": "priority_changed",
        "recommended_issue_type": "Story",
        "recommended_priority": "P1",
        "confidence": 0.9,
        "reason": "Inconsistent.",
    }
    with pytest.raises(ValidationError):
        parse_triage_audit_event(payload)


@pytest.mark.unit
def test_parse_triage_completed_bug_requires_priority() -> None:
    from triage_service.observability.audit_events import parse_triage_audit_event

    payload: dict[str, Any] = {
        "event_type": "triage_completed",
        "run_id": "r2",
        "issue_key": "TJC-3",
        "project": "TJC",
        "source": "bug_created",
        "recommended_issue_type": "Bug",
        "recommended_priority": None,
        "confidence": 0.5,
        "reason": "Bug path.",
    }
    with pytest.raises(ValidationError):
        parse_triage_audit_event(payload)


@pytest.mark.unit
def test_parse_triage_failed_includes_category_and_message() -> None:
    from triage_service.observability.audit_events import parse_triage_audit_event

    payload: dict[str, Any] = {
        "event_type": "triage_failed",
        "run_id": "r3",
        "issue_key": "TJC-4",
        "project": "TJC",
        "source": "manual_trigger",
        "category": "jira_fetch_failed",
        "message": "HTTP 503 from Jira.",
    }
    event = parse_triage_audit_event(payload)
    assert event.event_type == "triage_failed"
    assert event.category == "jira_fetch_failed"


@pytest.mark.unit
def test_parse_triage_failed_accepts_optional_telemetry() -> None:
    from triage_service.observability.audit_events import (
        dump_triage_audit_event,
        parse_triage_audit_event,
    )

    payload: dict[str, Any] = {
        "event_type": "triage_failed",
        "run_id": "r3",
        "issue_key": "TJC-4",
        "project": "TJC",
        "source": "manual_trigger",
        "category": "jira_fetch_failed",
        "message": "HTTP 503 from Jira.",
        "telemetry": {"http_attempts": 2, "http_status": 503},
    }
    event = parse_triage_audit_event(payload)
    dumped = dump_triage_audit_event(event)
    assert dumped["telemetry"] == {"http_attempts": 2, "http_status": 503}


@pytest.mark.unit
def test_parse_rejects_unknown_event_type() -> None:
    from triage_service.observability.audit_events import parse_triage_audit_event

    payload: dict[str, Any] = {
        "event_type": "unknown_phase",
        "run_id": "r",
        "issue_key": "TJC-1",
        "project": "TJC",
        "source": "bug_created",
    }
    with pytest.raises(ValidationError):
        parse_triage_audit_event(payload)


@pytest.mark.unit
def test_dump_triage_audit_event_json_serializable() -> None:
    from triage_service.observability.audit_events import (
        dump_triage_audit_event,
        parse_triage_audit_event,
    )

    payload: dict[str, Any] = {
        "event_type": "triage_completed",
        "run_id": "r4",
        "issue_key": "TJC-5",
        "project": "TJC",
        "source": "bug_created",
        "recommended_issue_type": "Bug",
        "recommended_priority": "P0",
        "confidence": 0.99,
        "reason": "Data loss risk.",
    }
    event = parse_triage_audit_event(payload)
    dumped = dump_triage_audit_event(event)
    json.dumps(dumped)
