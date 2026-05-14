"""LangFuse-backed sink for canonical triage lifecycle audit events."""

from __future__ import annotations

import logging
from typing import Protocol, cast

from langfuse import Langfuse

from triage_service.observability.audit_events import TriageAuditEvent, dump_triage_audit_event
from triage_service.observability.audit_store import AuditStore
from triage_service.observability.langfuse_inference_tracing import stable_langfuse_trace_id

LOGGER = logging.getLogger(__name__)


class _LangfuseEventClient(Protocol):
    def create_event(
        self,
        *,
        name: str,
        trace_id: str,
        metadata: dict[str, object],
    ) -> object:
        """Record one event attached to a LangFuse trace."""


class LangfuseAuditStore(AuditStore):
    """Writes validated triage lifecycle events to LangFuse as trace events."""

    def __init__(self, client: _LangfuseEventClient | None) -> None:
        self._client = client

    def record(self, event: TriageAuditEvent) -> None:
        if self._client is None:
            return
        payload = dump_triage_audit_event(event)
        run_id = str(payload["run_id"])
        try:
            self._client.create_event(
                name=str(payload["event_type"]),
                trace_id=stable_langfuse_trace_id(run_id),
                metadata=payload,
            )
        except Exception:
            LOGGER.warning("Langfuse audit event emission failed", exc_info=True)


def build_langfuse_audit_store(
    *,
    public_key: str | None,
    secret_key: str | None,
    base_url: str | None = None,
) -> LangfuseAuditStore:
    """Construct LangFuse audit sink when credentials are configured."""
    pk = str(public_key or "").strip()
    sk = str(secret_key or "").strip()
    if not pk or not sk:
        return LangfuseAuditStore(client=None)
    bu = str(base_url or "").strip() or None
    client = Langfuse(public_key=pk, secret_key=sk, base_url=bu)
    return LangfuseAuditStore(client=cast(_LangfuseEventClient, client))
