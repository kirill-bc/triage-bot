"""Unit tests for audit-store fan-out contract."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from triage_service.observability.audit_events import (
    ClassificationCompletedAuditEvent,
    TriageAuditEvent,
)


@dataclass
class _RecordingStore:
    recorded: list[TriageAuditEvent] = field(default_factory=list)

    def record(self, event: TriageAuditEvent) -> None:
        self.recorded.append(event)


@pytest.mark.unit
def test_composite_audit_store_records_event_to_all_children() -> None:
    from triage_service.observability.audit_store import CompositeAuditStore

    left = _RecordingStore()
    right = _RecordingStore()
    event = ClassificationCompletedAuditEvent(
        event_type="classification_completed",
        run_id="r1",
        issue_key="TJC-1",
        project="TJC",
        source="manual_trigger",
        recommended_issue_type="Bug",
        confidence=0.82,
        reason="Regression behavior.",
    )

    store = CompositeAuditStore([left, right])
    store.record(event)

    assert left.recorded == [event]
    assert right.recorded == [event]


@pytest.mark.unit
def test_composite_audit_store_with_no_children_is_noop() -> None:
    from triage_service.observability.audit_store import CompositeAuditStore

    event = ClassificationCompletedAuditEvent(
        event_type="classification_completed",
        run_id="r1",
        issue_key="TJC-1",
        project="TJC",
        source="manual_trigger",
        recommended_issue_type="Story",
        confidence=0.71,
        reason="Feature request language.",
    )
    store = CompositeAuditStore([])
    store.record(event)
