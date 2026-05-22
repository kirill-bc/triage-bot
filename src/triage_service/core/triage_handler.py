"""Synchronous per-issue triage orchestration (classification then optional priority)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import logging
import os
from time import perf_counter
from typing import Protocol, cast

from triage_service.adapters.image_context_extractor import (
    ImageContext,
    ImageContextExtractionResult,
    ImageContextExtractor,
    NoOpImageContextExtractor,
)
from triage_service.adapters.jira_issue_fetcher import (
    FetchedIssue,
    JiraIssueFetcher,
    JiraIssueFetchError,
)
from triage_service.adapters.zendesk_ticket_fetcher import (
    ZendeskTicketFetchError,
    ZendeskTicketFetcher,
)
from triage_service.adapters.openrouter_inference_client import (
    OpenRouterInferenceClient,
    OpenRouterInferenceError,
)
from triage_service.core.policy_context import PolicyContext
from triage_service.core.settings import AppSettings
from triage_service.core.prompt_composer import (
    compose_classification_prompt,
    compose_classification_system_prompt,
    compose_priority_prompt,
    compose_priority_system_prompt,
)
from triage_service.core.triage_fallback import (
    ProjectNotAllowedError,
    TriageFailure,
    fallback_for_exception,
)
from triage_service.core.triage_recommendation_parser import (
    ClassificationStepOutput,
    InvalidTriageRecommendationError,
    PriorityStepOutput,
    TriageRecommendation,
    classification_story_to_final,
    merge_bug_classification_with_priority,
    parse_classification_step_text,
    parse_priority_step_text,
)
from triage_service.observability.audit_events import (
    ClassificationCompletedAuditEvent,
    ImageAttachmentExtractionDetail,
    ImageContextExtractedAuditEvent,
    PriorityCompletedAuditEvent,
    TriageCompletedAuditEvent,
    TriageFailedAuditEvent,
    TriageSourceLiteral,
)
from triage_service.observability.audit_store import AuditStore, CompositeAuditStore
from triage_service.observability.langfuse_inference_tracing import LangfuseInferenceTracer

LOGGER = logging.getLogger(__name__)


def _failure_category_for_jira_http_status(status: int) -> str:
    if status == 429:
        return "http_rate_limited"
    if status in (502, 503, 504):
        return "http_transient"
    if 400 <= status < 600:
        return "http_error"
    return "http_error"


def _failure_category_for_jira_fetch(exc: JiraIssueFetchError) -> str | None:
    if exc.http_status is not None:
        return _failure_category_for_jira_http_status(exc.http_status)
    if exc.transport_timeout:
        return "timeout"
    if exc.transport_error_kind:
        return exc.transport_error_kind
    if exc.attempts is None and exc.http_status is None:
        return "configuration"
    return None


def _jira_fetch_error_telemetry(exc: JiraIssueFetchError) -> dict[str, object]:
    meta: dict[str, object] = {"boundary": "jira_http"}
    if exc.attempts is not None:
        meta["http_attempts"] = exc.attempts
    if exc.http_status is not None:
        meta["http_status"] = exc.http_status
    if exc.transport_timeout is not None:
        meta["transport_timeout"] = exc.transport_timeout
    if exc.transport_error_kind is not None:
        meta["transport_error_kind"] = exc.transport_error_kind
    fc = _failure_category_for_jira_fetch(exc)
    if fc is not None:
        meta["failure_category"] = fc
    return meta


def _openrouter_error_telemetry(exc: OpenRouterInferenceError) -> dict[str, object]:
    meta: dict[str, object] = {"boundary": "openrouter"}
    if exc.attempts is not None:
        meta["http_attempts"] = exc.attempts
    if exc.http_status is not None:
        meta["http_status"] = exc.http_status
    if exc.transport_timeout is not None:
        meta["transport_timeout"] = exc.transport_timeout
    if exc.transport_error_kind is not None:
        meta["transport_error_kind"] = exc.transport_error_kind
    if exc.failure_category is not None:
        meta["failure_category"] = exc.failure_category
    return meta


def _audit_telemetry_for_exception(exc: BaseException) -> dict[str, object] | None:
    """Attach HTTP/retry hints for triage_failed audit events when available."""
    if isinstance(exc, JiraIssueFetchError):
        return _jira_fetch_error_telemetry(exc)
    if isinstance(exc, OpenRouterInferenceError):
        return _openrouter_error_telemetry(exc)
    if isinstance(exc, InvalidTriageRecommendationError):
        return {"boundary": "model_output_parse", "failure_category": "invalid_model_output"}
    if isinstance(exc, ProjectNotAllowedError):
        return {"boundary": "policy_validation", "failure_category": "project_not_allowed"}
    return None


def _p0_p4_rank(label: str | None) -> int | None:
    if label is None:
        return None
    s = str(label).strip().upper()
    if len(s) != 2 or s[0] != "P" or s[1] not in "01234":
        return None
    return int(s[1])


def _image_context_telemetry(
    extraction: ImageContextExtractionResult | None,
) -> dict[str, object] | None:
    if extraction is None:
        return None
    return {
        "image_context_attachments_considered": extraction.attachments_considered,
        "image_context_attachments_extracted": extraction.attachments_extracted,
    }


def _merge_telemetry(*parts: dict[str, object] | None) -> dict[str, object] | None:
    merged: dict[str, object] = {}
    for part in parts:
        if part:
            merged.update(part)
    return merged or None


def _triage_completed_telemetry(
    *,
    issue: FetchedIssue,
    recommendation: TriageRecommendation,
    image_extraction: ImageContextExtractionResult | None = None,
) -> dict[str, object] | None:
    image_telemetry = _image_context_telemetry(image_extraction)
    if recommendation.recommended_issue_type != "Bug":
        return image_telemetry
    rec_pri = recommendation.recommended_priority
    if rec_pri is None:
        return image_telemetry
    orig_rank = _p0_p4_rank(issue.priority)
    rec_rank = _p0_p4_rank(str(rec_pri))
    if orig_rank is None or rec_rank is None:
        return image_telemetry
    if rec_rank < orig_rank:
        signal = "prioritize"
    elif rec_rank > orig_rank:
        signal = "deescalate"
    else:
        signal = "aligned"
    priority_telemetry = {
        "priority_signal": signal,
        "jira_priority": str(issue.priority).strip() if issue.priority is not None else "",
        "would_post_jira_comment": signal == "deescalate",
    }
    return _merge_telemetry(image_telemetry, priority_telemetry)


@dataclass(frozen=True, slots=True)
class _TriageInferenceSteps:
    classification: ClassificationStepOutput
    priority: PriorityStepOutput | None
    recommendation: TriageRecommendation


@dataclass(frozen=True, slots=True)
class TriageSyncResult:
    """Synchronous triage outcome plus optional vision preprocessing from the same run."""

    outcome: TriageRecommendation | TriageFailure
    image_extraction: ImageContextExtractionResult | None = None
    classification: ClassificationStepOutput | None = None
    priority: PriorityStepOutput | None = None


class TriageRunner(Protocol):
    """Callable surface used by :mod:`triage_api` (implemented by :class:`TriageHandler`)."""

    def run_sync(
        self,
        issue_key: str,
        project: str,
        source: str,
        *,
        run_id: str,
    ) -> TriageSyncResult:
        """Return recommendation or failure plus optional vision preprocessing metadata."""


class TriageActionExecutor(Protocol):
    """Applies triage outcome to Jira (labels, comments) or records failures."""

    def apply_triage_outcome(
        self,
        *,
        issue: FetchedIssue | None,
        issue_key: str,
        project: str,
        source: str,
        outcome: TriageRecommendation | TriageFailure,
        run_id: str,
    ) -> None:
        """``issue`` is None when triage failed before fetch or fetch failed."""


class NoOpTriageActionExecutor:
    """No-op executor when Jira REST target (cloud id or base URL) and user email are not set."""

    def apply_triage_outcome(
        self,
        *,
        issue: FetchedIssue | None,
        issue_key: str,
        project: str,
        source: str,
        outcome: TriageRecommendation | TriageFailure,
        run_id: str,
    ) -> None:
        _ = run_id
        return None


def _env_truthy(name: str) -> bool:
    token = os.environ.get(name, "").strip().lower()
    return token in ("1", "true", "yes", "on")


class LocalMockTriageRunner:
    """Deterministic local runner for container smoke checks without Jira/OpenRouter."""

    def __init__(self, *, allowed_projects: Sequence[str]) -> None:
        self._allowed = frozenset(allowed_projects)

    def run_sync(
        self,
        issue_key: str,
        project: str,
        source: str,
        *,
        run_id: str,
    ) -> TriageSyncResult:
        _ = (issue_key, source, run_id)
        if project not in self._allowed:
            msg = f"Project {project} is not allowed for triage."
            return TriageSyncResult(outcome=fallback_for_exception(ProjectNotAllowedError(msg)))
        return TriageSyncResult(
            outcome=TriageRecommendation(
                recommended_issue_type="Story",
                recommended_priority=None,
                confidence=1.0,
                reason="Local mock mode recommendation (external Jira/OpenRouter calls disabled).",
            ),
        )

    def flush_inference_telemetry(self) -> None:
        return None


class TriageHandler:
    """Fetch issue, run sequential model calls, parse, and delegate to the action executor."""

    def __init__(
        self,
        *,
        allowed_projects: Sequence[str],
        fetcher: JiraIssueFetcher,
        inference: OpenRouterInferenceClient,
        policy: PolicyContext,
        executor: TriageActionExecutor,
        inference_tracer: LangfuseInferenceTracer | None = None,
        audit_store: AuditStore | None = None,
        image_context_extractor: ImageContextExtractor | None = None,
        zendesk_fetcher: ZendeskTicketFetcher | None = None,
        settings: AppSettings,
    ) -> None:
        self._allowed = frozenset(allowed_projects)
        self._settings = settings
        self._fetcher = fetcher
        self._inference = inference
        self._policy = policy
        self._executor = executor
        self._inference_tracer = inference_tracer or LangfuseInferenceTracer(None)
        self._audit_store = audit_store if audit_store is not None else CompositeAuditStore(())
        self._image_context_extractor = (
            image_context_extractor
            if image_context_extractor is not None
            else NoOpImageContextExtractor()
        )
        self._zendesk_fetcher = zendesk_fetcher

    def flush_inference_telemetry(self) -> None:
        """Flush Langfuse buffers for inference traces and Langfuse-backed audit sinks."""
        self._inference_tracer.flush()

    def run_sync(
        self,
        issue_key: str,
        project: str,
        source: str,
        *,
        run_id: str,
    ) -> TriageSyncResult:
        """Run the full pipeline for one issue; always notifies ``executor`` before returning."""
        issue: FetchedIssue | None = None
        image_extraction: ImageContextExtractionResult | None = None
        tracer = self._inference_tracer
        with tracer.triage_run_session(run_id=run_id):
            try:
                self._ensure_project_allowed(project)
                fetch_start = perf_counter()
                try:
                    issue = self._fetcher.fetch(issue_key, run_id=run_id)
                    issue = self._enrich_with_zendesk(issue, run_id=run_id)
                finally:
                    self._log_stage_timing(
                        stage="jira_fetch",
                        run_id=run_id,
                        issue_key=issue_key,
                        project=project,
                        source=source,
                        started_at=fetch_start,
                    )
                with tracer.triage_issue_trace(
                    run_id=run_id,
                    issue_key=issue.issue_key,
                    project=project,
                ):
                    image_extraction = self._extract_image_contexts(
                        issue,
                        run_id=run_id,
                        project=project,
                        source=source,
                    )
                    steps = self._triage_fetched_issue(
                        issue,
                        run_id=run_id,
                        project=project,
                        source=source,
                        image_contexts=image_extraction.contexts,
                        image_extraction=image_extraction,
                    )
            except Exception as exc:
                failure = fallback_for_exception(exc)
                self._record_triage_failed_audit(
                    issue_key=issue_key,
                    project=project,
                    source=source,
                    run_id=run_id,
                    failure=failure,
                    exc=exc,
                    image_extraction=image_extraction,
                )
                action_start = perf_counter()
                try:
                    self._executor.apply_triage_outcome(
                        issue=issue,
                        issue_key=issue_key,
                        project=project,
                        source=source,
                        outcome=failure,
                        run_id=run_id,
                    )
                finally:
                    self._log_stage_timing(
                        stage="jira_action",
                        run_id=run_id,
                        issue_key=issue_key,
                        project=project,
                        source=source,
                        started_at=action_start,
                    )
                return TriageSyncResult(
                    outcome=failure,
                    image_extraction=image_extraction,
                )
            action_start = perf_counter()
            try:
                self._executor.apply_triage_outcome(
                    issue=issue,
                    issue_key=issue_key,
                    project=project,
                    source=source,
                    outcome=steps.recommendation,
                    run_id=run_id,
                )
            finally:
                self._log_stage_timing(
                    stage="jira_action",
                    run_id=run_id,
                    issue_key=issue_key,
                    project=project,
                    source=source,
                    started_at=action_start,
                )
            return TriageSyncResult(
                outcome=steps.recommendation,
                image_extraction=image_extraction,
                classification=steps.classification,
                priority=steps.priority,
            )

    def _enrich_with_zendesk(self, issue: FetchedIssue, *, run_id: str) -> FetchedIssue:
        fetcher = self._zendesk_fetcher
        if fetcher is None or not fetcher.enabled:
            return issue
        try:
            tickets = fetcher.fetch_linked_tickets(issue, run_id=run_id)
        except ZendeskTicketFetchError:
            LOGGER.exception(
                "Zendesk enrichment failed",
                extra={"issue_key": issue.issue_key, "run_id": run_id},
            )
            return issue
        if not tickets:
            return issue
        return issue.model_copy(update={"zendesk_tickets": tickets})

    def run_sync_on_fetched(
        self,
        *,
        issue: FetchedIssue,
        project: str,
        source: str,
        run_id: str,
        image_contexts: list[ImageContext] | None = None,
        image_extraction: ImageContextExtractionResult | None = None,
    ) -> TriageSyncResult:
        """Run classify then optional priority on a fetched issue.

        Same executor notifications as ``run_sync`` (e.g. NoOp for offline runs).
        Optional ``image_contexts`` supports benchmark replay with pre-extracted vision text.
        """
        tracer = self._inference_tracer
        with tracer.triage_run_session(run_id=run_id):
            try:
                self._ensure_project_allowed(project)
                with tracer.triage_issue_trace(
                    run_id=run_id,
                    issue_key=issue.issue_key,
                    project=project,
                ):
                    steps = self._triage_fetched_issue(
                        issue,
                        run_id=run_id,
                        project=project,
                        source=source,
                        image_contexts=image_contexts,
                        image_extraction=image_extraction,
                    )
            except Exception as exc:
                failure = fallback_for_exception(exc)
                self._record_triage_failed_audit(
                    issue_key=issue.issue_key,
                    project=project,
                    source=source,
                    run_id=run_id,
                    failure=failure,
                    exc=exc,
                    image_extraction=image_extraction,
                )
                action_start = perf_counter()
                try:
                    self._executor.apply_triage_outcome(
                        issue=issue,
                        issue_key=issue.issue_key,
                        project=project,
                        source=source,
                        outcome=failure,
                        run_id=run_id,
                    )
                finally:
                    self._log_stage_timing(
                        stage="jira_action",
                        run_id=run_id,
                        issue_key=issue.issue_key,
                        project=project,
                        source=source,
                        started_at=action_start,
                    )
                return TriageSyncResult(
                    outcome=failure,
                    image_extraction=image_extraction,
                )
            action_start = perf_counter()
            try:
                self._executor.apply_triage_outcome(
                    issue=issue,
                    issue_key=issue.issue_key,
                    project=project,
                    source=source,
                    outcome=steps.recommendation,
                    run_id=run_id,
                )
            finally:
                self._log_stage_timing(
                    stage="jira_action",
                    run_id=run_id,
                    issue_key=issue.issue_key,
                    project=project,
                    source=source,
                    started_at=action_start,
                )
            return TriageSyncResult(
                outcome=steps.recommendation,
                image_extraction=image_extraction,
                classification=steps.classification,
                priority=steps.priority,
            )

    def _record_triage_failed_audit(
        self,
        *,
        issue_key: str,
        project: str,
        source: str,
        run_id: str,
        failure: TriageFailure,
        exc: BaseException | None = None,
        image_extraction: ImageContextExtractionResult | None = None,
    ) -> None:
        resilience = _audit_telemetry_for_exception(exc) if exc is not None else None
        telemetry = _merge_telemetry(
            _image_context_telemetry(image_extraction),
            resilience,
        )
        audit_source = cast(TriageSourceLiteral, source)
        self._audit_store.record(
            TriageFailedAuditEvent(
                event_type="triage_failed",
                run_id=run_id,
                issue_key=issue_key,
                project=project,
                source=audit_source,
                category=failure.category,
                message=failure.message,
                telemetry=telemetry,
            ),
        )
        if telemetry:
            LOGGER.info(
                "triage_resilience_notice",
                extra={
                    "event_type": "triage_resilience_notice",
                    "run_id": run_id,
                    "issue_key": issue_key,
                    "project": project,
                    "source": source,
                    "triage_failure_category": failure.category,
                    **telemetry,
                },
            )

    def _ensure_project_allowed(self, project: str) -> None:
        if project not in self._allowed:
            msg = f"Project {project} is not allowed for triage."
            raise ProjectNotAllowedError(msg)

    def _extract_image_contexts(
        self,
        issue: FetchedIssue,
        *,
        run_id: str,
        project: str,
        source: str,
    ) -> ImageContextExtractionResult:
        extract_start = perf_counter()
        audit_source = cast(TriageSourceLiteral, source)
        result = ImageContextExtractionResult()
        try:
            with self._inference_tracer.image_context_extraction() as finish_img:
                try:
                    result = self._image_context_extractor.extract(issue, run_id=run_id)
                    finish_img(
                        attachments_considered=result.attachments_considered,
                        attachments_extracted=result.attachments_extracted,
                        total_bytes=result.total_bytes,
                        total_vision_cost=result.total_vision_cost,
                    )
                except Exception:
                    LOGGER.exception(
                        "Image context extraction failed unexpectedly",
                        extra={"issue_key": issue.issue_key, "run_id": run_id},
                    )
                    finish_img()
        finally:
            self._log_stage_timing(
                stage="image_context_extraction",
                run_id=run_id,
                issue_key=issue.issue_key,
                project=project,
                source=source,
                started_at=extract_start,
            )
        if result.attachments_considered > 0 or result.contexts:
            per_attachment = [
                ImageAttachmentExtractionDetail(
                    attachment_id=metric.attachment_id,
                    filename=metric.filename,
                    latency_ms=metric.latency_ms,
                    bytes_fetched=metric.bytes_fetched,
                    extraction_failure=metric.extraction_failure,
                )
                for metric in result.per_attachment
            ]
            self._audit_store.record(
                ImageContextExtractedAuditEvent(
                    event_type="image_context_extracted",
                    run_id=run_id,
                    issue_key=issue.issue_key,
                    project=project,
                    source=audit_source,
                    attachments_considered=result.attachments_considered,
                    attachments_extracted=result.attachments_extracted,
                    total_bytes=result.total_bytes,
                    total_vision_cost=result.total_vision_cost,
                    per_attachment=per_attachment,
                ),
            )
        return result

    def _triage_fetched_issue(
        self,
        issue: FetchedIssue,
        *,
        run_id: str,
        project: str,
        source: str,
        image_contexts: list[ImageContext] | None = None,
        image_extraction: ImageContextExtractionResult | None = None,
    ) -> _TriageInferenceSteps:
        audit_source = cast(TriageSourceLiteral, source)
        tracer = self._inference_tracer
        model_id = self._inference.effective_model_id
        cls_messages = _classification_messages(
            issue,
            self._policy,
            settings=self._settings,
            image_contexts=image_contexts,
        )
        with tracer.model_generation(
            step="classification",
            model=model_id,
            messages=cls_messages,
            model_parameters={"temperature": 0.2},
        ) as finish_cls:
            cls_start = perf_counter()
            try:
                cls_result = self._inference.chat_completion_with_details(
                    cls_messages,
                    run_id=run_id,
                )
                cls_text = cls_result.content
                classification = parse_classification_step_text(cls_text)
            finally:
                self._log_stage_timing(
                    stage="classification_inference",
                    run_id=run_id,
                    issue_key=issue.issue_key,
                    project=project,
                    source=source,
                    started_at=cls_start,
                )
            finish_cls(
                cls_text,
                {"parsed": classification.model_dump(mode="json")},
                usage_details=cls_result.usage_details,
                cost_details=cls_result.cost_details,
            )
        self._audit_store.record(
            ClassificationCompletedAuditEvent(
                event_type="classification_completed",
                run_id=run_id,
                issue_key=issue.issue_key,
                project=project,
                source=audit_source,
                recommended_issue_type=classification.recommended_issue_type,
                confidence=classification.confidence,
                reason=classification.reason,
            ),
        )
        if classification.recommended_issue_type == "Story":
            final_rec = classification_story_to_final(classification)
            self._audit_store.record(
                TriageCompletedAuditEvent(
                    event_type="triage_completed",
                    run_id=run_id,
                    issue_key=issue.issue_key,
                    project=project,
                    source=audit_source,
                    recommended_issue_type=final_rec.recommended_issue_type,
                    recommended_priority=final_rec.recommended_priority,
                    confidence=final_rec.confidence,
                    reason=final_rec.reason,
                    telemetry=_triage_completed_telemetry(
                        issue=issue,
                        recommendation=final_rec,
                        image_extraction=image_extraction,
                    ),
                ),
            )
            return _TriageInferenceSteps(
                classification=classification,
                priority=None,
                recommendation=final_rec,
            )
        pri_messages = _priority_messages(
            issue,
            self._policy,
            settings=self._settings,
            image_contexts=image_contexts,
        )
        with tracer.model_generation(
            step="priority",
            model=model_id,
            messages=pri_messages,
            model_parameters={"temperature": 0.2},
        ) as finish_pri:
            pri_start = perf_counter()
            try:
                pri_result = self._inference.chat_completion_with_details(
                    pri_messages,
                    run_id=run_id,
                )
                pri_text = pri_result.content
                priority = parse_priority_step_text(pri_text)
            finally:
                self._log_stage_timing(
                    stage="priority_inference",
                    run_id=run_id,
                    issue_key=issue.issue_key,
                    project=project,
                    source=source,
                    started_at=pri_start,
                )
            finish_pri(
                pri_text,
                {"parsed": priority.model_dump(mode="json")},
                usage_details=pri_result.usage_details,
                cost_details=pri_result.cost_details,
            )
        self._audit_store.record(
            PriorityCompletedAuditEvent(
                event_type="priority_completed",
                run_id=run_id,
                issue_key=issue.issue_key,
                project=project,
                source=audit_source,
                recommended_priority=priority.recommended_priority,
                confidence=priority.confidence,
                reason=priority.reason,
            ),
        )
        merged = merge_bug_classification_with_priority(classification, priority)
        self._audit_store.record(
            TriageCompletedAuditEvent(
                event_type="triage_completed",
                run_id=run_id,
                issue_key=issue.issue_key,
                project=project,
                source=audit_source,
                recommended_issue_type=merged.recommended_issue_type,
                recommended_priority=merged.recommended_priority,
                confidence=merged.confidence,
                reason=merged.reason,
                telemetry=_triage_completed_telemetry(
                    issue=issue,
                    recommendation=merged,
                    image_extraction=image_extraction,
                ),
            ),
        )
        return _TriageInferenceSteps(
            classification=classification,
            priority=priority,
            recommendation=merged,
        )

    def _log_stage_timing(
        self,
        *,
        stage: str,
        run_id: str,
        issue_key: str,
        project: str,
        source: str,
        started_at: float,
    ) -> None:
        elapsed_ms = max((perf_counter() - started_at) * 1000.0, 0.0)
        LOGGER.info(
            "triage_stage_timing",
            extra={
                "event_type": "triage_stage_timing",
                "stage": stage,
                "latency_ms": round(elapsed_ms, 3),
                "run_id": run_id,
                "issue_key": issue_key,
                "project": project,
                "source": source,
            },
        )


def build_default_triage_handler(
    *,
    post_mismatch_comments: bool = True,
    apply_to_jira: bool = True,
) -> TriageRunner:
    """Build handler from settings, policy, and Jira executor if Jira env is set."""
    from triage_service.adapters.image_context_extractor import build_image_context_extractor
    from triage_service.adapters.jira_action_executor import JiraTriageActionExecutor
    from triage_service.core.policy_context import load_policy_context
    from triage_service.core.settings import load_settings
    from triage_service.observability.observability_wiring import build_triage_observability

    settings = load_settings()
    if _env_truthy("TRIAGE_LOCAL_MOCK_MODE"):
        return LocalMockTriageRunner(allowed_projects=settings.allowed_projects)
    policy = load_policy_context()
    fetcher = JiraIssueFetcher(settings)
    obs = build_triage_observability(settings)
    image_extractor = build_image_context_extractor(
        settings,
        fetcher,
        inference_tracer=obs.inference_tracer,
    )
    zendesk_fetcher = ZendeskTicketFetcher(settings)
    inference = OpenRouterInferenceClient(settings)
    cloud_id_configured = settings.jira_cloud_id and str(settings.jira_cloud_id).strip()
    if (
        apply_to_jira
        and cloud_id_configured
        and settings.jira_user_email
        and str(settings.jira_user_email).strip()
    ):
        executor: TriageActionExecutor = JiraTriageActionExecutor(
            settings,
            post_mismatch_comments=post_mismatch_comments,
        )
    else:
        executor = NoOpTriageActionExecutor()
    return TriageHandler(
        allowed_projects=settings.allowed_projects,
        fetcher=fetcher,
        inference=inference,
        policy=policy,
        executor=executor,
        inference_tracer=obs.inference_tracer,
        audit_store=obs.audit_store,
        image_context_extractor=image_extractor,
        zendesk_fetcher=zendesk_fetcher,
        settings=settings,
    )


def _classification_messages(
    issue: FetchedIssue,
    policy: PolicyContext,
    *,
    settings: AppSettings,
    image_contexts: list[ImageContext] | None = None,
) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": compose_classification_system_prompt(settings=settings)},
        {
            "role": "user",
            "content": compose_classification_prompt(
                policy,
                issue,
                settings=settings,
                image_contexts=image_contexts,
            ),
        },
    ]


def _priority_messages(
    issue: FetchedIssue,
    policy: PolicyContext,
    *,
    settings: AppSettings,
    image_contexts: list[ImageContext] | None = None,
) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": compose_priority_system_prompt(settings=settings)},
        {
            "role": "user",
            "content": compose_priority_prompt(
                policy,
                issue,
                settings=settings,
                image_contexts=image_contexts,
            ),
        },
    ]
