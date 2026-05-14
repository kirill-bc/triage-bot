"""Compose inference tracing, audit fan-out, and Langfuse flush for triage runs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from langfuse import Langfuse

from triage_service.core.settings import AppSettings
from triage_service.observability.audit_store import AuditStore, CompositeAuditStore
from triage_service.observability.langfuse_audit_store import LangfuseAuditStore
from triage_service.observability.langfuse_inference_tracing import LangfuseInferenceTracer
from triage_service.observability.structured_logger_audit_store import StructuredLoggerAuditStore


class NoOpAuditStore:
    """Audit sink that discards events (all audit flags off or no backing stores)."""

    def record(self, event: object) -> None:
        return None


@dataclass(frozen=True)
class TriageObservability:
    """Bundled observability for :class:`~triage_service.core.triage_handler.TriageHandler`."""

    inference_tracer: LangfuseInferenceTracer
    audit_store: AuditStore

    def flush(self) -> None:
        """Flush Langfuse-backed buffers (shared client covers inference + audit paths)."""
        self.inference_tracer.flush()


def build_triage_observability(settings: AppSettings) -> TriageObservability:
    """Build tracer and audit fan-out from ``TRIAGE_AUDIT_*`` and Langfuse credential flags."""
    pk = str(settings.langfuse_public_key or "").strip()
    sk = str(settings.langfuse_secret_key or "").strip()
    bu = str(settings.langfuse_base_url or "").strip() or None
    langfuse_client: Langfuse | None = None
    if pk and sk:
        langfuse_client = Langfuse(public_key=pk, secret_key=sk, base_url=bu)

    inference_tracer = LangfuseInferenceTracer(
        langfuse_client,
        redact_model_input=settings.audit_redact_model_input,
        redact_model_output=settings.audit_redact_model_output,
    )

    stores: list[AuditStore] = []
    if settings.audit_structured_log_enabled:
        stores.append(StructuredLoggerAuditStore())
    if settings.audit_langfuse_enabled and langfuse_client is not None:
        stores.append(LangfuseAuditStore(client=cast(Any, langfuse_client)))

    if not stores:
        audit_store: AuditStore = NoOpAuditStore()
    elif len(stores) == 1:
        audit_store = stores[0]
    else:
        audit_store = CompositeAuditStore(stores)

    return TriageObservability(inference_tracer=inference_tracer, audit_store=audit_store)
