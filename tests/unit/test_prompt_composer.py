"""Unit tests for prompt_composer: split classification vs priority inputs."""

import pytest
from jira_issue_fetcher import FetchedIssue
from policy_context import PolicyContext

from prompt_composer import compose_classification_prompt, compose_priority_prompt


@pytest.mark.unit
def test_classification_prompt_includes_bug_policy_and_issue_excludes_priority_text() -> None:
    policy = PolicyContext(
        bug_definition="BUGPOLICY_UNIQUE_ALPHA",
        priority_definition="PRIORITYPOLICY_UNIQUE_BETA",
    )
    issue = FetchedIssue(
        issue_key="TJC-1",
        summary="Login fails",
        description="Steps to reproduce",
        issue_type="Bug",
        priority="P2",
        reporter="support@example.com",
    )
    text = compose_classification_prompt(policy, issue)
    assert "BUGPOLICY_UNIQUE_ALPHA" in text
    assert "TJC-1" in text and "Login fails" in text
    assert "PRIORITYPOLICY_UNIQUE_BETA" not in text
    assert "## Role" in text and "TriageBot" in text
    assert "JSON field `reason`" in text


@pytest.mark.unit
def test_priority_prompt_includes_priority_policy_and_issue_excludes_bug_text() -> None:
    policy = PolicyContext(
        bug_definition="BUGPOLICY_UNIQUE_GAMMA",
        priority_definition="PRIORITYPOLICY_UNIQUE_DELTA",
    )
    issue = FetchedIssue(
        issue_key="BC-99",
        summary="Crash on save",
        description=None,
        issue_type="Story",
        priority=None,
        reporter="Jane Doe",
    )
    text = compose_priority_prompt(policy, issue)
    assert "PRIORITYPOLICY_UNIQUE_DELTA" in text
    assert "BC-99" in text and "Crash on save" in text
    assert "BUGPOLICY_UNIQUE_GAMMA" not in text
    assert "## Role" in text and "TriageBot" in text and "still" in text
    assert "JSON field `reason`" in text


@pytest.mark.unit
def test_classification_prompt_shows_placeholder_when_description_and_priority_missing() -> None:
    policy = PolicyContext(bug_definition="x", priority_definition="y")
    issue = FetchedIssue(
        issue_key="K-1",
        summary="S",
        description=None,
        issue_type="Bug",
        priority=None,
        reporter="r",
    )
    text = compose_classification_prompt(policy, issue)
    assert "Current Jira priority: (none)" in text
    assert "Description:\n(none)" in text
