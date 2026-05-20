"""Extract text context from issue image attachments for prompt enrichment."""

from __future__ import annotations

import base64
import re
from time import perf_counter
from typing import Any, Protocol

from pydantic import BaseModel, Field

from triage_service.adapters.jira_issue_fetcher import (
    AttachmentRef,
    FetchedIssue,
    JiraIssueFetchError,
    JiraIssueFetcher,
)
from triage_service.adapters.openrouter_inference_client import (
    OpenRouterInferenceClient,
    OpenRouterInferenceError,
)
from triage_service.core.settings import AppSettings
from triage_service.core.vision_prompt_composer import (
    compose_vision_system_prompt,
    compose_vision_user_instruction,
)
from triage_service.observability.langfuse_inference_tracing import LangfuseInferenceTracer

_DEFAULT_MAX_BYTES_PER_IMAGE = 5 * 1024 * 1024
_IMAGE_MIME_PREFIX = "image/"
_FILENAME_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")


class ImageContext(BaseModel):
    """Vision-derived context for one attachment."""

    attachment_id: str
    filename: str
    transcript: str | None = None
    summary: str | None = None
    extraction_failure: str | None = None


class ImageAttachmentMetric(BaseModel):
    """Per-attachment metrics from one vision preprocessing attempt."""

    attachment_id: str
    filename: str
    latency_ms: float = Field(ge=0.0)
    bytes_fetched: int = Field(ge=0, default=0)
    extraction_failure: str | None = None
    vision_cost: float | None = Field(default=None, ge=0.0)


class ImageContextExtractionResult(BaseModel):
    """Contexts plus aggregate extraction telemetry for observability."""

    contexts: list[ImageContext] = Field(default_factory=list)
    attachments_considered: int = Field(ge=0, default=0)
    attachments_extracted: int = Field(ge=0, default=0)
    total_bytes: int = Field(ge=0, default=0)
    total_vision_cost: float | None = Field(default=None, ge=0.0)
    per_attachment: list[ImageAttachmentMetric] = Field(default_factory=list)


def build_cli_image_context_summary(
    *,
    enabled: bool,
    extraction: ImageContextExtractionResult | None,
) -> dict[str, object]:
    """Compact attachment summary for manual CLI smoke output (no full transcripts)."""
    if not enabled:
        return {"enabled": False}
    if extraction is None:
        return {
            "enabled": True,
            "attachments_considered": 0,
            "attachments_extracted": 0,
            "total_bytes": 0,
            "attachments": [],
        }
    attachments: list[dict[str, object]] = []
    for ctx in extraction.contexts:
        row: dict[str, object] = {
            "attachment_id": ctx.attachment_id,
            "filename": ctx.filename,
        }
        if ctx.extraction_failure:
            row["status"] = "failed"
            row["failure"] = ctx.extraction_failure
        else:
            row["status"] = "ok"
            if ctx.summary:
                row["summary"] = ctx.summary
        attachments.append(row)
    return {
        "enabled": True,
        "attachments_considered": extraction.attachments_considered,
        "attachments_extracted": extraction.attachments_extracted,
        "total_bytes": extraction.total_bytes,
        "attachments": attachments,
    }


class ImageContextExtractor(Protocol):
    """Produces image contexts for an issue (feature-flagged implementations)."""

    def extract(self, issue: FetchedIssue, *, run_id: str) -> ImageContextExtractionResult:
        """Return extracted contexts; never raise for per-image failures."""


class NoOpImageContextExtractor:
    """Feature-off path: no vision calls, empty context list."""

    def extract(self, issue: FetchedIssue, *, run_id: str) -> ImageContextExtractionResult:
        _ = (issue, run_id)
        return ImageContextExtractionResult()


def build_image_context_extractor(
    settings: AppSettings,
    jira_fetcher: JiraIssueFetcher,
    *,
    vision_client: OpenRouterInferenceClient | None = None,
    inference_tracer: LangfuseInferenceTracer | None = None,
) -> ImageContextExtractor:
    """Return NoOp when disabled; otherwise a vision-backed extractor using TRIAGE_VISION_MODEL."""
    if not settings.triage_image_context_enabled:
        return NoOpImageContextExtractor()
    inference = vision_client or OpenRouterInferenceClient(
        settings,
        model_override=settings.triage_vision_model,
        http_timeout_seconds=settings.triage_image_context_timeout_seconds,
    )
    return OpenRouterVisionImageContextExtractor(
        settings=settings,
        jira_fetcher=jira_fetcher,
        inference_client=inference,
        inference_tracer=inference_tracer or LangfuseInferenceTracer(None),
        max_attachments=settings.triage_image_context_max_attachments,
        max_bytes_per_image=settings.triage_image_context_max_bytes_per_image,
    )


def issue_has_inline_images(issue: FetchedIssue) -> bool:
    """Return whether the issue has description-inline image attachments eligible for vision."""
    return any(ref.inline and _is_image_attachment(ref) for ref in issue.attachments)


def _is_image_attachment(ref: AttachmentRef) -> bool:
    mime = (ref.mime_type or "").strip().lower()
    if mime.startswith(_IMAGE_MIME_PREFIX):
        return True
    name = ref.filename.lower()
    return any(name.endswith(ext) for ext in _FILENAME_IMAGE_EXTENSIONS)


def _attachment_size_desc_sort_key(ref: AttachmentRef) -> float:
    if ref.size_bytes is None:
        # Unknown size from Jira: keep ahead of known-large files when max_attachments caps.
        return float("-inf")
    return float(-ref.size_bytes)


def _select_image_attachments(
    attachments: list[AttachmentRef],
    *,
    max_attachments: int,
) -> list[AttachmentRef]:
    # Description-only mode: process only images explicitly referenced in ADF.
    inline_images = [
        ref for ref in attachments if ref.inline and _is_image_attachment(ref)
    ]
    ordered = sorted(inline_images, key=_attachment_size_desc_sort_key)
    return ordered[:max_attachments]


def _sniff_image_mime_from_bytes(data: bytes) -> str | None:
    if len(data) < 4:
        return None
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(data) >= 3 and data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if len(data) >= 6 and data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:2] == b"BM":
        return "image/bmp"
    return None


def _is_probably_non_image_payload(data: bytes) -> bool:
    head = data[:256].lstrip()
    if not head:
        return True
    if head.startswith((b"<", b"{", b"[")):
        return True
    lowered = head[:32].lower()
    if lowered.startswith(b"<!doctype") or lowered.startswith(b"<html"):
        return True
    return False


def _resolve_image_mime_type(ref: AttachmentRef) -> str | None:
    mime = (ref.mime_type or "").strip().lower()
    if mime.startswith(_IMAGE_MIME_PREFIX):
        return mime
    name = ref.filename.lower()
    if name.endswith(".png"):
        return "image/png"
    if name.endswith(".jpg") or name.endswith(".jpeg"):
        return "image/jpeg"
    if name.endswith(".gif"):
        return "image/gif"
    if name.endswith(".webp"):
        return "image/webp"
    if name.endswith(".bmp"):
        return "image/bmp"
    return None


def _vision_cost_from_details(cost_details: dict[str, float] | None) -> float | None:
    if not cost_details:
        return None
    total = cost_details.get("total")
    if isinstance(total, (int, float)) and not isinstance(total, bool):
        return float(total)
    cost = cost_details.get("cost")
    if isinstance(cost, (int, float)) and not isinstance(cost, bool):
        return float(cost)
    return float(sum(cost_details.values()))


def _parse_vision_response(content: str) -> tuple[str | None, str | None, str | None]:
    """Return (transcript, summary, parse_failure)."""
    text = content.strip()
    if not text:
        return None, None, "empty vision response"
    transcript_match = re.search(
        r"TRANSCRIPT:\s*(.*?)(?:\n\s*SUMMARY:|\Z)",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    summary_match = re.search(
        r"SUMMARY:\s*(.*)\Z",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if transcript_match is None or summary_match is None:
        return None, None, "vision response missing TRANSCRIPT or SUMMARY sections"
    transcript = transcript_match.group(1).strip()
    summary = summary_match.group(1).strip()
    if not transcript or not summary:
        return None, None, "vision response has empty TRANSCRIPT or SUMMARY"
    if transcript.lower() == "(none)":
        transcript = ""
    return transcript, summary, None


def _attachment_extraction_metric(
    ref: AttachmentRef,
    *,
    started: float,
    context: ImageContext,
    bytes_fetched: int = 0,
    vision_cost: float | None = None,
) -> tuple[ImageContext, ImageAttachmentMetric]:
    latency_ms = max((perf_counter() - started) * 1000.0, 0.0)
    metric = ImageAttachmentMetric(
        attachment_id=ref.id,
        filename=ref.filename,
        latency_ms=latency_ms,
        bytes_fetched=bytes_fetched,
        extraction_failure=context.extraction_failure,
        vision_cost=vision_cost,
    )
    return context, metric


def _validate_image_bytes_for_vision(
    image_bytes: bytes,
    base: ImageContext,
    *,
    max_bytes: int,
) -> ImageContext | None:
    if len(image_bytes) > max_bytes:
        return base.model_copy(
            update={
                "extraction_failure": (
                    f"exceeds size limit ({len(image_bytes)} bytes > {max_bytes})"
                ),
            },
        )
    if _is_probably_non_image_payload(image_bytes):
        return base.model_copy(
            update={
                "extraction_failure": (
                    "attachment payload is not image binary "
                    "(got HTML/JSON/text — check Jira attachment fetch)"
                ),
            },
        )
    if _sniff_image_mime_from_bytes(image_bytes) is None:
        return base.model_copy(
            update={"extraction_failure": "unrecognized image file format"},
        )
    return None


def _build_vision_messages(
    issue: FetchedIssue,
    image_bytes: bytes,
    mime_type: str,
    *,
    settings: AppSettings,
) -> list[dict[str, Any]]:
    encoded = base64.standard_b64encode(image_bytes).decode("ascii")
    data_url = f"data:{mime_type};base64,{encoded}"
    return [
        {"role": "system", "content": compose_vision_system_prompt(settings=settings)},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": compose_vision_user_instruction(issue, settings=settings)},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        },
    ]


class OpenRouterVisionImageContextExtractor:
    """Vision preprocessor: Jira attachment bytes → transcript + UI summary via OpenRouter."""

    def __init__(
        self,
        *,
        settings: AppSettings,
        jira_fetcher: JiraIssueFetcher,
        inference_client: OpenRouterInferenceClient,
        inference_tracer: LangfuseInferenceTracer | None = None,
        max_attachments: int = 5,
        max_bytes_per_image: int = _DEFAULT_MAX_BYTES_PER_IMAGE,
    ) -> None:
        self._settings = settings
        self._jira_fetcher = jira_fetcher
        self._inference = inference_client
        self._inference_tracer = inference_tracer or LangfuseInferenceTracer(None)
        self._max_attachments = max_attachments
        self._max_bytes_per_image = max_bytes_per_image

    def extract(self, issue: FetchedIssue, *, run_id: str) -> ImageContextExtractionResult:
        selected = _select_image_attachments(
            issue.attachments,
            max_attachments=self._max_attachments,
        )
        contexts: list[ImageContext] = []
        per_attachment: list[ImageAttachmentMetric] = []
        total_bytes = 0
        vision_costs: list[float] = []
        for ref in selected:
            context, metric = self._extract_one(issue, ref, run_id=run_id)
            contexts.append(context)
            per_attachment.append(metric)
            total_bytes += metric.bytes_fetched
            if metric.vision_cost is not None:
                vision_costs.append(metric.vision_cost)
        extracted = sum(1 for ctx in contexts if ctx.extraction_failure is None)
        total_vision_cost = sum(vision_costs) if vision_costs else None
        return ImageContextExtractionResult(
            contexts=contexts,
            attachments_considered=len(selected),
            attachments_extracted=extracted,
            total_bytes=total_bytes,
            total_vision_cost=total_vision_cost,
            per_attachment=per_attachment,
        )

    def _extract_one(
        self,
        issue: FetchedIssue,
        ref: AttachmentRef,
        *,
        run_id: str,
    ) -> tuple[ImageContext, ImageAttachmentMetric]:
        started = perf_counter()
        base = ImageContext(
            attachment_id=ref.id,
            filename=ref.filename,
        )
        resolved_mime = _resolve_image_mime_type(ref)
        if resolved_mime is None:
            failed = base.model_copy(update={"extraction_failure": "unsupported MIME type"})
            return _attachment_extraction_metric(ref, started=started, context=failed)
        try:
            image_bytes = self._jira_fetcher.fetch_attachment_bytes(ref.id, run_id=run_id)
        except JiraIssueFetchError as exc:
            failed = base.model_copy(
                update={"extraction_failure": f"attachment fetch failed: {exc}"},
            )
            return _attachment_extraction_metric(ref, started=started, context=failed)
        bytes_fetched = len(image_bytes)
        validation_failure = _validate_image_bytes_for_vision(
            image_bytes,
            base,
            max_bytes=self._max_bytes_per_image,
        )
        if validation_failure is not None:
            return _attachment_extraction_metric(
                ref,
                started=started,
                context=validation_failure,
                bytes_fetched=bytes_fetched,
            )
        return self._vision_extract_one(
            issue,
            ref,
            image_bytes=image_bytes,
            mime_type=resolved_mime,
            base=base,
            run_id=run_id,
            started=started,
            bytes_fetched=bytes_fetched,
        )

    def _vision_extract_one(
        self,
        issue: FetchedIssue,
        ref: AttachmentRef,
        *,
        image_bytes: bytes,
        mime_type: str,
        base: ImageContext,
        run_id: str,
        started: float,
        bytes_fetched: int,
    ) -> tuple[ImageContext, ImageAttachmentMetric]:
        messages = _build_vision_messages(
            issue,
            image_bytes,
            mime_type,
            settings=self._settings,
        )
        model_id = self._inference.effective_model_id
        with self._inference_tracer.vision_generation(
            model=model_id,
            messages=messages,
            model_parameters={"temperature": 0.0},
            attachment_id=ref.id,
            filename=ref.filename,
        ) as finish_vision:
            try:
                result = self._inference.chat_completion_with_details(
                    messages,
                    run_id=run_id,
                    temperature=0.0,
                )
            except OpenRouterInferenceError as exc:
                finish_vision("", {"extraction_failure": str(exc)})
                failed = base.model_copy(
                    update={"extraction_failure": f"vision extraction failed: {exc}"},
                )
                return _attachment_extraction_metric(
                    ref,
                    started=started,
                    context=failed,
                    bytes_fetched=bytes_fetched,
                )
            vision_cost = _vision_cost_from_details(result.cost_details)
            transcript, summary, parse_failure = _parse_vision_response(result.content)
            finish_vision(
                result.content,
                {
                    "attachment_id": ref.id,
                    "filename": ref.filename,
                    "parse_failure": parse_failure,
                },
                usage_details=result.usage_details,
                cost_details=result.cost_details,
            )
            if parse_failure is not None:
                failed = base.model_copy(update={"extraction_failure": parse_failure})
                return _attachment_extraction_metric(
                    ref,
                    started=started,
                    context=failed,
                    bytes_fetched=bytes_fetched,
                    vision_cost=vision_cost,
                )
            success = base.model_copy(
                update={"transcript": transcript, "summary": summary},
            )
            return _attachment_extraction_metric(
                ref,
                started=started,
                context=success,
                bytes_fetched=bytes_fetched,
                vision_cost=vision_cost,
            )
