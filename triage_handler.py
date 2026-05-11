"""Synchronous per-issue triage orchestration (classification then optional priority)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from jira_issue_fetcher import FetchedIssue, JiraIssueFetcher
from openrouter_inference_client import OpenRouterInferenceClient
from policy_context import PolicyContext
from prompt_composer import compose_classification_prompt, compose_priority_prompt
from triage_fallback import ProjectNotAllowedError, TriageFailure, fallback_for_exception
from triage_recommendation_parser import (
    TriageRecommendation,
    classification_story_to_final,
    merge_bug_classification_with_priority,
    parse_classification_step_text,
    parse_priority_step_text,
)

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


class TriageRunner(Protocol):
    """Callable surface used by :mod:`triage_api` (implemented by :class:`TriageHandler`)."""

    def run_sync(
        self,
        issue_key: str,
        project: str,
        source: str,
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
    ) -> None:
        """``issue`` is None when triage failed before fetch or fetch failed."""


class NoOpTriageActionExecutor:
    """Executor stub until :mod:`jira_action_executor` is implemented."""

    def apply_triage_outcome(
        self,
        *,
        issue: FetchedIssue | None,
        issue_key: str,
        project: str,
        source: str,
        outcome: TriageRecommendation | TriageFailure,
    ) -> None:
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
    ) -> None:
        self._allowed = frozenset(allowed_projects)
        self._fetcher = fetcher
        self._inference = inference
        self._policy = policy
        self._executor = executor

    def run_sync(
        self,
        issue_key: str,
        project: str,
        source: str,
    ) -> TriageRecommendation | TriageFailure:
        """Run the full pipeline for one issue; always notifies ``executor`` before returning."""
        issue: FetchedIssue | None = None
        try:
            self._ensure_project_allowed(project)
            issue = self._fetcher.fetch(issue_key)
            outcome = self._triage_fetched_issue(issue)
        except Exception as exc:
            failure = fallback_for_exception(exc)
            self._executor.apply_triage_outcome(
                issue=issue,
                issue_key=issue_key,
                project=project,
                source=source,
                outcome=failure,
            )
            return failure
        self._executor.apply_triage_outcome(
            issue=issue,
            issue_key=issue_key,
            project=project,
            source=source,
            outcome=outcome,
        )
        return outcome

    def _ensure_project_allowed(self, project: str) -> None:
        if project not in self._allowed:
            msg = f"Project {project} is not allowed for triage."
            raise ProjectNotAllowedError(msg)

    def _triage_fetched_issue(self, issue: FetchedIssue) -> TriageRecommendation:
        cls_text = self._inference.chat_completion(
            _classification_messages(issue, self._policy),
        )
        classification = parse_classification_step_text(cls_text)
        if classification.recommended_issue_type == "Story":
            return classification_story_to_final(classification)
        pri_text = self._inference.chat_completion(_priority_messages(issue, self._policy))
        priority = parse_priority_step_text(pri_text)
        return merge_bug_classification_with_priority(classification, priority)


def build_default_triage_handler() -> TriageHandler:
    """Build a handler from env settings, core config, bundled policy, and no-op executor."""
    from core_config import load_triage_core_config
    from policy_context import load_policy_context
    from settings import load_settings

    settings = load_settings()
    core = load_triage_core_config()
    policy = load_policy_context()
    fetcher = JiraIssueFetcher(settings)
    inference = OpenRouterInferenceClient(settings)
    return TriageHandler(
        allowed_projects=core.allowed_projects,
        fetcher=fetcher,
        inference=inference,
        policy=policy,
        executor=NoOpTriageActionExecutor(),
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
