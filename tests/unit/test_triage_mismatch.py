"""Deterministic mismatch flags (Jira state vs recommendation)."""

from __future__ import annotations

import pytest

from jira_issue_fetcher import FetchedIssue
from triage_mismatch import TriageMismatchFlags, compute_mismatch_flags
from triage_recommendation_parser import TriageRecommendation


def _issue(**overrides: object) -> FetchedIssue:
    base: dict[str, object] = {
        "issue_key": "TJC-1",
        "summary": "s",
        "description": None,
        "issue_type": "Bug",
        "priority": "P2",
        "reporter": "r",
    }
    base.update(overrides)
    return FetchedIssue.model_validate(base)


def _rec(**overrides: object) -> TriageRecommendation:
    base: dict[str, object] = {
        "recommended_issue_type": "Bug",
        "recommended_priority": "P2",
        "confidence": 0.5,
        "reason": "because",
    }
    base.update(overrides)
    return TriageRecommendation.model_validate(base)


@pytest.mark.unit
def test_compute_flags_no_mismatch_when_jira_matches_bug_priority() -> None:
    flags = compute_mismatch_flags(_issue(), _rec())
    assert flags == TriageMismatchFlags(type_mismatch=False, priority_mismatch=False)
    assert flags.any_mismatch() is False


@pytest.mark.unit
def test_compute_flags_type_mismatch_only_story_recommendation_skips_priority() -> None:
    flags = compute_mismatch_flags(
        _issue(issue_type="Bug"),
        _rec(recommended_issue_type="Story", recommended_priority=None),
    )
    assert flags.type_mismatch is True
    assert flags.priority_mismatch is False


@pytest.mark.unit
def test_compute_flags_priority_mismatch_when_bug_path_and_labels_differ() -> None:
    flags = compute_mismatch_flags(_issue(priority="P1"), _rec(recommended_priority="P2"))
    assert flags.type_mismatch is False
    assert flags.priority_mismatch is True


@pytest.mark.unit
def test_compute_flags_priority_case_insensitive_match() -> None:
    flags = compute_mismatch_flags(_issue(priority="p2"), _rec(recommended_priority="P2"))
    assert flags.priority_mismatch is False


@pytest.mark.unit
def test_compute_flags_missing_jira_priority_is_priority_mismatch_on_bug_path() -> None:
    flags = compute_mismatch_flags(_issue(priority=None), _rec())
    assert flags.type_mismatch is False
    assert flags.priority_mismatch is True


@pytest.mark.unit
def test_compute_flags_empty_jira_priority_string_is_priority_mismatch() -> None:
    flags = compute_mismatch_flags(_issue(priority="  "), _rec())
    assert flags.priority_mismatch is True


@pytest.mark.unit
def test_compute_flags_jira_task_vs_recommended_story_is_type_mismatch() -> None:
    flags = compute_mismatch_flags(
        _issue(issue_type="Task"),
        _rec(recommended_issue_type="Story", recommended_priority=None),
    )
    assert flags.type_mismatch is True
