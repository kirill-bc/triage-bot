"""Deterministic mismatch flags between Jira issue state and triage recommendations.

Executor and comment composition should use these flags (plus ``reason`` and optional
``confidence`` from the model), not a model-supplied action enum.
"""

from __future__ import annotations

from dataclasses import dataclass

from triage_service.adapters.jira_issue_fetcher import FetchedIssue
from triage_service.core.triage_recommendation_parser import TriageRecommendation


@dataclass(frozen=True)
class TriageMismatchFlags:
    """Whether Jira type and/or priority disagree with the merged recommendation."""

    type_mismatch: bool
    priority_mismatch: bool

    def any_mismatch(self) -> bool:
        return self.type_mismatch or self.priority_mismatch


def compute_mismatch_flags(
    issue: FetchedIssue,
    recommendation: TriageRecommendation,
) -> TriageMismatchFlags:
    """Compare Jira fields to the recommendation (type always; priority only on Bug path)."""
    jira_type = issue.issue_type
    rec_type = recommendation.recommended_issue_type
    type_mismatch = not _issue_type_labels_match(jira_type, rec_type)
    rec_pri = recommendation.recommended_priority
    if rec_type != "Bug" or rec_pri is None:
        return TriageMismatchFlags(type_mismatch=type_mismatch, priority_mismatch=False)
    pri_bad = not _priority_labels_match(issue.priority, rec_pri)
    return TriageMismatchFlags(type_mismatch=type_mismatch, priority_mismatch=pri_bad)


def _issue_type_labels_match(jira_issue_type: str, recommended: str) -> bool:
    j = jira_issue_type.strip().casefold()
    r = recommended.strip().casefold()
    return j == r


def _priority_labels_match(jira_priority: str | None, recommended: str) -> bool:
    if jira_priority is None or not str(jira_priority).strip():
        return False
    return str(jira_priority).strip().casefold() == recommended.strip().casefold()
