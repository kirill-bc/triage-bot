"""LangFuse spans for OpenRouter inference steps (classification and priority)."""

from __future__ import annotations

import hashlib
import logging
import uuid
from collections.abc import Callable, Generator
from contextlib import contextmanager
from typing import Any, Literal, cast

from langfuse import Langfuse, propagate_attributes
from langfuse.types import TraceContext

from triage_service.observability.log_payload_guard import (
    DEFAULT_MAX_LOG_STRING_CHARS,
    truncate_log_string,
    truncate_logging_value,
)
from triage_service.observability.payload_redaction import (
    sanitize_chat_messages,
    sanitize_model_output_text,
    sanitize_vision_messages,
)

LOGGER = logging.getLogger(__name__)

_LANGFUSE_SESSION_ID_MAX_LEN = 199

InferenceStepName = Literal["classification", "priority"]
GenerationFinish = Callable[..., None]
ImageContextExtractionFinish = Callable[..., None]


def _apply_langfuse_generation_update(
    gen: object,
    raw: str,
    meta: dict[str, Any],
    *,
    redact_model_output: bool,
    usage_details: dict[str, int] | None,
    cost_details: dict[str, float] | None,
) -> None:
    out_text = sanitize_model_output_text(raw, redact=redact_model_output)
    out_text, out_trunc = truncate_log_string(
        out_text,
        max_chars=DEFAULT_MAX_LOG_STRING_CHARS,
    )
    merged_meta = dict(meta)
    if redact_model_output:
        merged_meta = {
            **merged_meta,
            "redacted_output": True,
            "output_length": len(raw),
        }
    merged_any, meta_trunc = truncate_logging_value(
        merged_meta,
        max_string_chars=DEFAULT_MAX_LOG_STRING_CHARS,
    )
    merged_meta = cast(dict[str, Any], merged_any)
    if out_trunc or meta_trunc:
        merged_meta["log_payload_truncated"] = True
    update_data: dict[str, Any] = {"output": out_text, "metadata": merged_meta}
    if usage_details is not None:
        update_data["usage_details"] = usage_details
    if cost_details is not None:
        update_data["cost_details"] = cost_details
    try:
        update = getattr(gen, "update")
        update(**update_data)
    except Exception:
        LOGGER.warning("Langfuse generation update failed", exc_info=True)


def _safe_current_trace_context(client: object) -> TraceContext | None:
    """Return explicit trace context when both current trace/span ids are available."""
    get_trace = getattr(client, "get_current_trace_id", None)
    get_observation = getattr(client, "get_current_observation_id", None)
    if not callable(get_trace) or not callable(get_observation):
        return None
    try:
        trace_id = get_trace()
        parent_span_id = get_observation()
    except Exception:
        return None
    if not isinstance(trace_id, str) or not trace_id.strip():
        return None
    if not isinstance(parent_span_id, str) or not parent_span_id.strip():
        return None
    return cast(
        TraceContext,
        {
            "trace_id": trace_id,
            "parent_span_id": parent_span_id,
        },
    )


def _start_current_observation(
    client: Langfuse,
    *,
    trace_context: TraceContext | None,
    **kwargs: Any,
) -> Any:
    """Start an observation, omitting ``trace_context`` when unavailable.

    Passing ``trace_context=None`` can detach observation parenting in some SDK paths.
    """
    if trace_context is None:
        return client.start_as_current_observation(**kwargs)
    return client.start_as_current_observation(trace_context=trace_context, **kwargs)


def stable_langfuse_trace_id(run_id: str) -> str:
    """Return a 32-char hex id suitable for :class:`langfuse.types.TraceContext`."""
    trimmed = run_id.strip()
    try:
        return str(uuid.UUID(trimmed)).replace("-", "")
    except ValueError:
        return hashlib.sha256(trimmed.encode("utf-8")).hexdigest()[:32]


def langfuse_session_id(run_id: str) -> str:
    """Return a Langfuse session id for grouping traces (US-ASCII, max 199 chars)."""
    trimmed = run_id.strip()
    if len(trimmed) <= _LANGFUSE_SESSION_ID_MAX_LEN:
        return trimmed
    return trimmed[:_LANGFUSE_SESSION_ID_MAX_LEN]


class LangfuseInferenceTracer:
    """Records triage root span plus per-step generation metadata (failure-safe)."""

    def __init__(
        self,
        client: Langfuse | None,
        *,
        redact_model_input: bool = False,
        redact_model_output: bool = True,
        redact_vision_transcript: bool = True,
    ) -> None:
        self._client = client
        self._redact_model_input = redact_model_input
        self._redact_model_output = redact_model_output
        self._redact_vision_transcript = redact_vision_transcript

    def flush(self) -> None:
        """Best-effort flush for short-lived processes (CLI, tests, serverless)."""
        if self._client is None:
            return
        try:
            self._client.flush()
        except Exception:
            LOGGER.warning("Langfuse flush failed", exc_info=True)

    @contextmanager
    def triage_run_session(self, *, run_id: str) -> Generator[None, None, None]:
        """Propagate Langfuse ``session_id`` for the full triage run (all traces/spans)."""
        if self._client is None:
            yield
            return
        try:
            with propagate_attributes(session_id=langfuse_session_id(run_id)):
                yield
        except Exception:
            LOGGER.warning("Langfuse triage_run session propagation failed", exc_info=True)
            yield

    @contextmanager
    def triage_issue_trace(
        self,
        *,
        run_id: str,
        issue_key: str,
        project: str,
    ) -> Generator[None, None, None]:
        if self._client is None:
            yield
            return
        try:
            with self._client.start_as_current_observation(
                name="triage_issue_pipeline",
                as_type="span",
                metadata={
                    "run_id": run_id,
                    "issue_key": issue_key,
                    "project": project,
                    "operation": "triage_issue",
                },
            ):
                yield
        except Exception:
            LOGGER.warning("Langfuse triage_issue span failed", exc_info=True)
            yield

    @contextmanager
    def image_context_extraction(self) -> Generator[ImageContextExtractionFinish, None, None]:
        def noop_finish(
            *,
            attachments_considered: int = 0,
            attachments_extracted: int = 0,
            total_bytes: int = 0,
            total_vision_cost: float | None = None,
        ) -> None:
            _ = (
                attachments_considered,
                attachments_extracted,
                total_bytes,
                total_vision_cost,
            )
            return None

        if self._client is None:
            yield noop_finish
            return
        try:
            trace_context = _safe_current_trace_context(self._client)
            with _start_current_observation(
                self._client,
                trace_context=trace_context,
                name="image_context_extraction",
                as_type="span",
                metadata={"operation": "image_context_extraction"},
            ) as span:

                def finish(
                    *,
                    attachments_considered: int,
                    attachments_extracted: int,
                    total_bytes: int,
                    total_vision_cost: float | None,
                ) -> None:
                    metadata: dict[str, Any] = {
                        "attachments_considered": attachments_considered,
                        "attachments_extracted": attachments_extracted,
                        "total_bytes": total_bytes,
                    }
                    if total_vision_cost is not None:
                        metadata["total_vision_cost"] = total_vision_cost
                    try:
                        update = getattr(span, "update")
                        update(metadata=metadata)
                    except Exception:
                        LOGGER.warning(
                            "Langfuse image_context_extraction update failed",
                            exc_info=True,
                        )

                yield finish
        except Exception:
            LOGGER.warning("Langfuse image_context_extraction span failed", exc_info=True)
            yield noop_finish

    @contextmanager
    def vision_generation(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        model_parameters: dict[str, Any],
        attachment_id: str,
        filename: str,
    ) -> Generator[GenerationFinish, None, None]:
        def noop_finish(
            _raw: str,
            _meta: dict[str, Any],
            *,
            usage_details: dict[str, int] | None = None,
            cost_details: dict[str, float] | None = None,
        ) -> None:
            _ = (usage_details, cost_details)
            return None

        if self._client is None:
            yield noop_finish
            return
        # Keep issue context (description, repro steps) verbatim in Langfuse; only
        # screenshot bytes are summarized. Transcript redaction is on generation output.
        traced_input = sanitize_vision_messages(messages, redact=False)
        gen_metadata: dict[str, Any] = {
            "operation": "inference_vision",
            "attachment_id": attachment_id,
            "filename": filename,
        }
        try:
            trace_context = _safe_current_trace_context(self._client)
            with _start_current_observation(
                self._client,
                trace_context=trace_context,
                name="inference_vision",
                as_type="generation",
                model=model,
                input=traced_input,
                model_parameters=model_parameters,
                metadata=gen_metadata,
            ) as gen:

                def finish(
                    raw: str,
                    meta: dict[str, Any],
                    *,
                    usage_details: dict[str, int] | None = None,
                    cost_details: dict[str, float] | None = None,
                ) -> None:
                    _apply_langfuse_generation_update(
                        gen,
                        raw,
                        meta,
                        redact_model_output=self._redact_vision_transcript,
                        usage_details=usage_details,
                        cost_details=cost_details,
                    )

                yield finish
        except Exception:
            LOGGER.warning("Langfuse vision generation span failed", exc_info=True)
            yield noop_finish

    @contextmanager
    def model_generation(
        self,
        *,
        step: InferenceStepName,
        model: str,
        messages: list[dict[str, str]],
        model_parameters: dict[str, Any],
    ) -> Generator[GenerationFinish, None, None]:
        def noop_finish(
            _raw: str,
            _meta: dict[str, Any],
            *,
            usage_details: dict[str, int] | None = None,
            cost_details: dict[str, float] | None = None,
        ) -> None:
            _ = (usage_details, cost_details)
            return None

        if self._client is None:
            yield noop_finish
            return
        gen_name = "inference_classification" if step == "classification" else "inference_priority"
        traced_input = sanitize_chat_messages(
            messages,
            redact=self._redact_model_input,
        )
        traced_input, input_trunc = truncate_logging_value(
            traced_input,
            max_string_chars=DEFAULT_MAX_LOG_STRING_CHARS,
        )
        gen_metadata: dict[str, Any] = {"operation": gen_name, "step": step}
        if input_trunc:
            gen_metadata["log_payload_truncated"] = True
        try:
            trace_context = _safe_current_trace_context(self._client)
            with _start_current_observation(
                self._client,
                trace_context=trace_context,
                name=gen_name,
                as_type="generation",
                model=model,
                input=traced_input,
                model_parameters=model_parameters,
                metadata=gen_metadata,
            ) as gen:

                def finish(
                    raw: str,
                    meta: dict[str, Any],
                    *,
                    usage_details: dict[str, int] | None = None,
                    cost_details: dict[str, float] | None = None,
                ) -> None:
                    _apply_langfuse_generation_update(
                        gen,
                        raw,
                        meta,
                        redact_model_output=self._redact_model_output,
                        usage_details=usage_details,
                        cost_details=cost_details,
                    )

                yield finish
        except Exception:
            LOGGER.warning("Langfuse generation span failed", exc_info=True)
            yield noop_finish


def build_langfuse_inference_tracer(
    *,
    public_key: str | None,
    secret_key: str | None,
    base_url: str | None = None,
    redact_model_input: bool = False,
    redact_model_output: bool = True,
) -> LangfuseInferenceTracer:
    """Construct a tracer when LangFuse keys are configured; otherwise a no-op tracer."""
    pk = str(public_key or "").strip()
    sk = str(secret_key or "").strip()
    if not pk or not sk:
        return LangfuseInferenceTracer(
            None,
            redact_model_input=redact_model_input,
            redact_model_output=redact_model_output,
        )
    bu = str(base_url or "").strip() or None
    client = Langfuse(public_key=pk, secret_key=sk, base_url=bu)
    return LangfuseInferenceTracer(
        client,
        redact_model_input=redact_model_input,
        redact_model_output=redact_model_output,
    )
