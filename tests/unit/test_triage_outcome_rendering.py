"""Unit tests for transport-independent triage outcome decisions."""

from __future__ import annotations

from typing import Any

import pytest

from triage_service.adapters.jira_issue_fetcher import FetchedIssue
from triage_service.adapters.triage_outcome_rendering import (
    AutoApplyPolicy,
    build_outcome_decision,
)
from triage_service.core.triage_recommendation_parser import TriageRecommendation


def _issue(**overrides: Any) -> FetchedIssue:
    values: dict[str, Any] = {
        "issue_key": "TJC-1",
        "summary": "Summary",
        "description": None,
        "issue_type": "Bug",
        "priority": "P2",
        "reporter": "Alice",
    }
    values.update(overrides)
    return FetchedIssue.model_validate(values)


def _recommendation(**overrides: Any) -> TriageRecommendation:
    values: dict[str, Any] = {
        "recommended_issue_type": "Bug",
        "recommended_priority": "P2",
        "confidence": 0.8,
        "reason": "Matches policy.",
    }
    values.update(overrides)
    return TriageRecommendation.model_validate(values)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("issue", "recommendation", "expected_labels", "expected_signal"),
    [
        (
            _issue(),
            _recommendation(),
            ["triagebot-reviewed"],
            None,
        ),
        (
            _issue(),
            _recommendation(
                recommended_issue_type="Story",
                recommended_priority=None,
            ),
            ["triagebot-reviewed", "triagebot-likely-story"],
            None,
        ),
        (
            _issue(priority="P3"),
            _recommendation(recommended_priority="P1"),
            ["triagebot-reviewed", "triagebot-priority-mismatch"],
            "prioritize",
        ),
        (
            _issue(priority="P1"),
            _recommendation(recommended_priority="P3"),
            ["triagebot-reviewed", "triagebot-priority-mismatch"],
            "deescalate",
        ),
    ],
)
def test_build_outcome_decision_selects_labels_and_comment_gate(
    issue: FetchedIssue,
    recommendation: TriageRecommendation,
    expected_labels: list[str],
    expected_signal: str | None,
) -> None:
    decision = build_outcome_decision(
        issue,
        recommendation,
        policy=AutoApplyPolicy(),
    )

    assert decision.labels == expected_labels
    assert decision.priority_signal == expected_signal
    assert decision.post_comment is (
        expected_signal is not None
        or recommendation.recommended_issue_type == "Story"
    )


@pytest.mark.unit
def test_build_outcome_decision_selects_enabled_priority_action() -> None:
    decision = build_outcome_decision(
        _issue(priority="P3"),
        _recommendation(recommended_priority="P1"),
        policy=AutoApplyPolicy(apply_escalation=True),
    )

    assert decision.apply_bug_to_story is False
    assert decision.apply_priority is not None
    assert decision.apply_priority.from_priority == "P3"
    assert decision.apply_priority.to_priority == "P1"


@pytest.mark.unit
def test_build_outcome_decision_selects_bug_to_story_only_for_bug_intake() -> None:
    recommendation = _recommendation(
        recommended_issue_type="Story",
        recommended_priority=None,
    )

    bug_decision = build_outcome_decision(
        _issue(issue_type="Bug"),
        recommendation,
        policy=AutoApplyPolicy(apply_bug_to_story=True),
    )
    task_decision = build_outcome_decision(
        _issue(issue_type="Task"),
        recommendation,
        policy=AutoApplyPolicy(apply_bug_to_story=True),
    )

    assert bug_decision.apply_bug_to_story is True
    assert task_decision.apply_bug_to_story is False
