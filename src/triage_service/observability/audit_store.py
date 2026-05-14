"""Audit event sinks used by handler/adapters to persist observability records."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from triage_service.observability.audit_events import TriageAuditEvent


class AuditStore(Protocol):
    """Minimal sink contract for validated triage lifecycle audit events."""

    def record(self, event: TriageAuditEvent) -> None:
        """Persist one audit event to the backing store."""


class CompositeAuditStore:
    """Fan-out sink that forwards each event to all configured stores."""

    def __init__(self, stores: Iterable[AuditStore]) -> None:
        self._stores = tuple(stores)

    def record(self, event: TriageAuditEvent) -> None:
        for store in self._stores:
            store.record(event)
