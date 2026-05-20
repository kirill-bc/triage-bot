"""Compose inference tracing, audit fan-out, and Langfuse flush for triage runs."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import os
from typing import Any, cast

from langfuse import Langfuse

from triage_service.core.settings import AppSettings
from triage_service.observability.audit_store import AuditStore, CompositeAuditStore
from triage_service.observability.langfuse_audit_store import LangfuseAuditStore
from triage_service.observability.langfuse_inference_tracing import LangfuseInferenceTracer
from triage_service.observability.structured_logger_audit_store import StructuredLoggerAuditStore

LOGGER = logging.getLogger(__name__)


def _langfuse_sdk_tracing_env_enabled() -> bool:
    """Match Langfuse SDK: tracing off only when env is the string ``false`` (case-insensitive)."""
    return os.environ.get("LANGFUSE_TRACING_ENABLED", "true").lower() != "false"


def _otel_sdk_disabled_env() -> bool:
    """True when ``OTEL_SDK_DISABLED`` matches OpenTelemetry / Langfuse disable semantics."""
    return os.environ.get("OTEL_SDK_DISABLED", "false").lower() == "true"


def observability_status_summary(settings: AppSettings) -> dict[str, bool]:
    """Safe booleans for health checks and logs (no secret values).

    ``langfuse_inference_enabled`` is true when both Langfuse keys are non-empty
    (the SDK client would be constructed). This does not prove outbound network
    reachability to Langfuse.
    """
    pk = str(settings.langfuse_public_key or "").strip()
    sk = str(settings.langfuse_secret_key or "").strip()
    bu = str(settings.langfuse_base_url or "").strip()
    langfuse_inference_enabled = bool(pk and sk)
    sdk_tracing_env = _langfuse_sdk_tracing_env_enabled()
    otel_disabled = _otel_sdk_disabled_env()
    export_env_ready = (
        langfuse_inference_enabled and sdk_tracing_env and not otel_disabled
    )
    return {
        "langfuse_public_key_present": bool(pk),
        "langfuse_secret_key_present": bool(sk),
        "langfuse_base_url_configured": bool(bu),
        "langfuse_prompt_management_enabled": settings.langfuse_prompt_management_enabled,
        "langfuse_inference_enabled": langfuse_inference_enabled,
        "langfuse_sdk_tracing_env_enabled": sdk_tracing_env,
        "otel_sdk_disabled": otel_disabled,
        "langfuse_export_env_ready": export_env_ready,
        "audit_langfuse_enabled": settings.audit_langfuse_enabled,
        "langfuse_audit_sink_enabled": (
            settings.audit_langfuse_enabled and langfuse_inference_enabled
        ),
        "audit_structured_log_enabled": settings.audit_structured_log_enabled,
    }


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
    summary = observability_status_summary(settings)
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
        redact_vision_transcript=settings.triage_audit_redact_image_transcript,
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

    log_extra: dict[str, object] = {"event_type": "triage_observability_config", **summary}
    if langfuse_client is not None:
        log_extra["langfuse_runtime_tracing_enabled"] = bool(
            getattr(langfuse_client, "_tracing_enabled", True),
        )
    LOGGER.info("triage_observability_config", extra=log_extra)

    return TriageObservability(inference_tracer=inference_tracer, audit_store=audit_store)
