"""Synchronous per-issue triage orchestration (classification then optional priority)."""

from __future__ import annotations

from collections.abc import Sequence
import logging
from time import perf_counter
from typing import Protocol, cast

from triage_service.adapters.jira_issue_fetcher import (
    FetchedIssue,
    JiraIssueFetcher,
    JiraIssueFetchError,
)
from triage_service.adapters.openrouter_inference_client import (
    OpenRouterInferenceClient,
    OpenRouterInferenceError,
)
from triage_service.core.policy_context import PolicyContext
from triage_service.core.prompt_composer import (
    compose_classification_prompt,
    compose_priority_prompt,
)
from triage_service.core.triage_fallback import (
    ProjectNotAllowedError,
    TriageFailure,
    fallback_for_exception,
)
from triage_service.core.triage_recommendation_parser import (
    InvalidTriageRecommendationError,
    TriageRecommendation,
    classification_story_to_final,
    merge_bug_classification_with_priority,
    parse_classification_step_text,
    parse_priority_step_text,
)
from triage_service.observability.audit_events import (
    ClassificationCompletedAuditEvent,
    PriorityCompletedAuditEvent,
    TriageCompletedAuditEvent,
    TriageFailedAuditEvent,
    TriageSourceLiteral,
)
from triage_service.observability.audit_store import AuditStore, CompositeAuditStore
from triage_service.observability.langfuse_inference_tracing import LangfuseInferenceTracer

_CLASSIFICATION_SYSTEM = (
    "You are a Jira triage assistant. Reply with a single JSON object only (no markdown fences). "
    "Keys: recommended_issue_type (Bug or Story), confidence (number from 0 to 1), "
    "reason (non-empty string explaining the classification). "
    "Do not include recommended_priority; priority is inferred in a separate step when type is Bug."
)

_PRIORITY_SYSTEM = (
    "You are a Jira triage assistant. Reply with a single JSON object only (no markdown fences). "
    "Keys: recommended_priority (P0, P1, P2, P3, or P4), confidence (0 to 1), "
    "reason (non-empty string explaining the priority assessment)."
)

LOGGER = logging.getLogger(__name__)


def _audit_telemetry_for_exception(exc: BaseException) -> dict[str, object] | None:
    """Attach HTTP/retry hints for triage_failed audit events when available."""
    if isinstance(exc, JiraIssueFetchError):
        meta: dict[str, object] = {"boundary": "jira_http"}
        if exc.attempts is not None:
            meta["http_attempts"] = exc.attempts
        if exc.http_status is not None:
            meta["http_status"] = exc.http_status
        if exc.transport_timeout is not None:
            meta["transport_timeout"] = exc.transport_timeout
        return meta
    if isinstance(exc, OpenRouterInferenceError):
        return {"boundary": "openrouter"}
    if isinstance(exc, InvalidTriageRecommendationError):
        return {"boundary": "model_output_parse"}
    if isinstance(exc, ProjectNotAllowedError):
        return {"boundary": "policy_validation"}
    return None


class TriageRunner(Protocol):
    """Callable surface used by :mod:`triage_api` (implemented by :class:`TriageHandler`)."""

    def run_sync(
        self,
        issue_key: str,
        project: str,
        source: str,
        *,
        run_id: str,
    ) -> TriageRecommendation | TriageFailure:
        """Return a recommendation or failure after executor side effects."""


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
    ) -> None:
        self._allowed = frozenset(allowed_projects)
        self._fetcher = fetcher
        self._inference = inference
        self._policy = policy
        self._executor = executor
        self._inference_tracer = inference_tracer or LangfuseInferenceTracer(None)
        self._audit_store = audit_store if audit_store is not None else CompositeAuditStore(())

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
    ) -> TriageRecommendation | TriageFailure:
        """Run the full pipeline for one issue; always notifies ``executor`` before returning."""
        issue: FetchedIssue | None = None
        try:
            self._ensure_project_allowed(project)
            fetch_start = perf_counter()
            try:
                issue = self._fetcher.fetch(issue_key, run_id=run_id)
            finally:
                self._log_stage_timing(
                    stage="jira_fetch",
                    run_id=run_id,
                    issue_key=issue_key,
                    project=project,
                    source=source,
                    started_at=fetch_start,
                )
            outcome = self._triage_fetched_issue(
                issue,
                run_id=run_id,
                project=project,
                source=source,
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
            return failure
        action_start = perf_counter()
        try:
            self._executor.apply_triage_outcome(
                issue=issue,
                issue_key=issue_key,
                project=project,
                source=source,
                outcome=outcome,
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
        return outcome

    def run_sync_on_fetched(
        self,
        *,
        issue: FetchedIssue,
        project: str,
        source: str,
        run_id: str,
    ) -> TriageRecommendation | TriageFailure:
        """Run classify then optional priority on a fetched issue.

        Same executor notifications as ``run_sync`` (e.g. NoOp for offline runs).
        """
        try:
            self._ensure_project_allowed(project)
            outcome = self._triage_fetched_issue(
                issue,
                run_id=run_id,
                project=project,
                source=source,
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
            return failure
        action_start = perf_counter()
        try:
            self._executor.apply_triage_outcome(
                issue=issue,
                issue_key=issue.issue_key,
                project=project,
                source=source,
                outcome=outcome,
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
        return outcome

    def _record_triage_failed_audit(
        self,
        *,
        issue_key: str,
        project: str,
        source: str,
        run_id: str,
        failure: TriageFailure,
        exc: BaseException | None = None,
    ) -> None:
        telemetry = _audit_telemetry_for_exception(exc) if exc is not None else None
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

    def _ensure_project_allowed(self, project: str) -> None:
        if project not in self._allowed:
            msg = f"Project {project} is not allowed for triage."
            raise ProjectNotAllowedError(msg)

    def _triage_fetched_issue(
        self,
        issue: FetchedIssue,
        *,
        run_id: str,
        project: str,
        source: str,
    ) -> TriageRecommendation:
        audit_source = cast(TriageSourceLiteral, source)
        tracer = self._inference_tracer
        model_id = self._inference.effective_model_id
        with tracer.triage_issue_trace(
            run_id=run_id,
            issue_key=issue.issue_key,
            project=project,
        ):
            cls_messages = _classification_messages(issue, self._policy)
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
                    ),
                )
                return final_rec
            pri_messages = _priority_messages(issue, self._policy)
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
                ),
            )
            return merged

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


def build_default_triage_handler() -> TriageHandler:
    """Build handler from settings, policy, and Jira executor if Jira env is set."""
    from triage_service.adapters.jira_action_executor import JiraTriageActionExecutor
    from triage_service.core.policy_context import load_policy_context
    from triage_service.core.settings import load_settings
    from triage_service.observability.observability_wiring import build_triage_observability

    settings = load_settings()
    policy = load_policy_context()
    fetcher = JiraIssueFetcher(settings)
    inference = OpenRouterInferenceClient(settings)
    cloud_id_configured = settings.jira_cloud_id and str(settings.jira_cloud_id).strip()
    if (
        cloud_id_configured
        and settings.jira_user_email
        and str(settings.jira_user_email).strip()
    ):
        executor: TriageActionExecutor = JiraTriageActionExecutor(settings)
    else:
        executor = NoOpTriageActionExecutor()
    obs = build_triage_observability(settings)
    return TriageHandler(
        allowed_projects=settings.allowed_projects,
        fetcher=fetcher,
        inference=inference,
        policy=policy,
        executor=executor,
        inference_tracer=obs.inference_tracer,
        audit_store=obs.audit_store,
    )


def _classification_messages(issue: FetchedIssue, policy: PolicyContext) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": _CLASSIFICATION_SYSTEM},
        {"role": "user", "content": compose_classification_prompt(policy, issue)},
    ]


def _priority_messages(issue: FetchedIssue, policy: PolicyContext) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": _PRIORITY_SYSTEM},
        {"role": "user", "content": compose_priority_prompt(policy, issue)},
    ]
