"""LangFuse spans for OpenRouter inference steps (classification and priority)."""

from __future__ import annotations

import hashlib
import logging
import uuid
from collections.abc import Callable, Generator
from contextlib import contextmanager
from typing import Any, Literal, cast

from langfuse import Langfuse

from triage_service.observability.log_payload_guard import (
    DEFAULT_MAX_LOG_STRING_CHARS,
    truncate_log_string,
    truncate_logging_value,
)
from triage_service.observability.payload_redaction import (
    sanitize_chat_messages,
    sanitize_model_output_text,
)

LOGGER = logging.getLogger(__name__)

InferenceStepName = Literal["classification", "priority"]
GenerationFinish = Callable[..., None]


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


def stable_langfuse_trace_id(run_id: str) -> str:
    """Return a 32-char hex id suitable for :class:`langfuse.types.TraceContext`."""
    trimmed = run_id.strip()
    try:
        return str(uuid.UUID(trimmed)).replace("-", "")
    except ValueError:
        return hashlib.sha256(trimmed.encode("utf-8")).hexdigest()[:32]


class LangfuseInferenceTracer:
    """Records triage root span plus per-step generation metadata (failure-safe)."""

    def __init__(
        self,
        client: Langfuse | None,
        *,
        redact_model_input: bool = True,
        redact_model_output: bool = True,
    ) -> None:
        self._client = client
        self._redact_model_input = redact_model_input
        self._redact_model_output = redact_model_output

    def flush(self) -> None:
        """Best-effort flush for short-lived processes (CLI, tests, serverless)."""
        if self._client is None:
            return
        try:
            self._client.flush()
        except Exception:
            LOGGER.warning("Langfuse flush failed", exc_info=True)

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
            with self._client.start_as_current_observation(
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
    redact_model_input: bool = True,
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
