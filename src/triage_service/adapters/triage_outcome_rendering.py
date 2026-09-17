"""Transport-independent triage outcome decisions.

Comment text is composed by Rule B from the ``comment`` inputs on the callback payload, so
nothing here renders copy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from triage_service.adapters.jira_issue_fetcher import FetchedIssue
from triage_service.core.triage_mismatch import compute_mismatch_flags
from triage_service.core.triage_recommendation_parser import TriageRecommendation

PrioritySignal = Literal["prioritize", "deescalate"]


@dataclass(frozen=True, slots=True)
class AutoApplyPolicy:
    """Enabled automatic Jira field mutations."""

    apply_deescalation: bool = False
    apply_escalation: bool = False
    apply_bug_to_story: bool = False


@dataclass(frozen=True, slots=True)
class PriorityAction:
    """Requested priority mutation."""

    from_priority: str
    to_priority: str


@dataclass(frozen=True, slots=True)
class OutcomeDecision:
    """Transport-independent labels, comment gate, and mutation directives."""

    labels: list[str]
    priority_signal: PrioritySignal | None
    post_comment: bool
    apply_bug_to_story: bool
    apply_priority: PriorityAction | None


def _p0_p4_rank(label: str | None) -> int | None:
    if label is None:
        return None
    normalized = str(label).strip().upper()
    if len(normalized) != 2 or normalized[0] != "P" or normalized[1] not in "01234":
        return None
    return int(normalized[1])


def _priority_signal(
    issue: FetchedIssue,
    recommendation: TriageRecommendation,
) -> PrioritySignal | None:
    if recommendation.recommended_issue_type != "Bug":
        return None
    recommended = recommendation.recommended_priority
    if recommended is None:
        return None
    original_rank = _p0_p4_rank(issue.priority)
    recommended_rank = _p0_p4_rank(str(recommended))
    if original_rank is None or recommended_rank is None:
        return None
    if recommended_rank < original_rank:
        return "prioritize"
    if recommended_rank > original_rank:
        return "deescalate"
    return None


def build_outcome_decision(
    issue: FetchedIssue,
    recommendation: TriageRecommendation,
    *,
    policy: AutoApplyPolicy,
) -> OutcomeDecision:
    """Select labels, comment behavior, and enabled field changes."""
    flags = compute_mismatch_flags(issue, recommendation)
    signal = _priority_signal(issue, recommendation)
    likely_story = (
        flags.type_mismatch and recommendation.recommended_issue_type == "Story"
    )
    labels = ["triagebot-reviewed"]
    if likely_story:
        labels.append("triagebot-likely-story")
    if signal is not None:
        labels.append("triagebot-priority-mismatch")

    apply_bug_to_story = (
        policy.apply_bug_to_story
        and likely_story
        and str(issue.issue_type).strip().upper() == "BUG"
    )
    apply_priority = _priority_action(issue, recommendation, signal, policy)
    return OutcomeDecision(
        labels=labels,
        priority_signal=signal,
        post_comment=likely_story or signal is not None,
        apply_bug_to_story=apply_bug_to_story,
        apply_priority=apply_priority,
    )


def _priority_action(
    issue: FetchedIssue,
    recommendation: TriageRecommendation,
    signal: PrioritySignal | None,
    policy: AutoApplyPolicy,
) -> PriorityAction | None:
    enabled = (
        signal == "prioritize"
        and policy.apply_escalation
        or signal == "deescalate"
        and policy.apply_deescalation
    )
    if not enabled or recommendation.recommended_priority is None:
        return None
    current = str(issue.priority).strip() if issue.priority is not None else ""
    return PriorityAction(
        from_priority=current,
        to_priority=str(recommendation.recommended_priority).strip(),
    )
