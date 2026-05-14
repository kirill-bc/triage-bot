"""Structured JSON logger sink for canonical triage lifecycle audit events."""

from __future__ import annotations

import json
import logging
from typing import Protocol

from triage_service.observability.audit_events import TriageAuditEvent, dump_triage_audit_event
from triage_service.observability.audit_store import AuditStore

LOGGER = logging.getLogger(__name__)


class _StructuredAuditLogger(Protocol):
    def info(self, msg: str, *args: object, **kwargs: object) -> object:
        """Emit one structured info log line."""


class StructuredLoggerAuditStore(AuditStore):
    """Writes validated triage audit events as JSON log lines."""

    def __init__(self, logger: _StructuredAuditLogger | None = None) -> None:
        self._logger = logger or LOGGER

    def record(self, event: TriageAuditEvent) -> None:
        payload = dump_triage_audit_event(event)
        try:
            self._logger.info(
                json.dumps(payload, separators=(",", ":"), sort_keys=True)
            )
        except Exception:
            LOGGER.warning("Structured audit log emission failed", exc_info=True)
