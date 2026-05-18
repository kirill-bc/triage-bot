"""Unit tests for prompt_composer: split classification vs priority inputs."""

import importlib
import json
import sys
from pathlib import Path

import pytest
from triage_service.adapters.jira_issue_fetcher import FetchedIssue
from triage_service.core.policy_context import PolicyContext

from triage_service.core.prompt_composer import (
    compose_classification_prompt,
    compose_priority_prompt,
)


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
        reproduction_steps="1) Open app\n2) Click login",
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
    assert "Reproduction steps" in text
    assert "1) Open app" in text


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
        reproduction_steps=None,
        issue_type="Bug",
        priority=None,
        reporter="r",
    )
    text = compose_classification_prompt(policy, issue)
    assert "Current Jira priority: (none)" in text
    assert "Description:\n(none)" in text
    assert "Reproduction steps:\n(none)" in text


@pytest.mark.unit
def test_prompt_composer_loads_templates_from_external_json_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    template_path = tmp_path / "prompt_templates.json"
    template_path.write_text(
        json.dumps(
            {
                "reason_for_humans": "CUSTOM_REASON_BLOCK\n\n",
                "classification_template": (
                    "CLASSIFY PREFIX\n"
                    "{reason_for_humans}"
                    "BUG POLICY:\n{bug_definition}\n\n"
                    "ISSUE DATA:\n{issue_block}\n"
                ),
                "priority_template": (
                    "PRIORITY PREFIX\n"
                    "{reason_for_humans}"
                    "PRIORITY POLICY:\n{priority_definition}\n\n"
                    "ISSUE DATA:\n{issue_block}\n"
                ),
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("TRIAGE_PROMPT_TEMPLATES_PATH", str(template_path))
    module = importlib.reload(sys.modules["triage_service.core.prompt_composer"])
    policy = PolicyContext(bug_definition="BUG_RULE", priority_definition="P_RULE")
    issue = FetchedIssue(
        issue_key="TJC-2",
        summary="Something broke",
        description="Detailed description",
        issue_type="Bug",
        priority="P1",
        reporter="agent@example.com",
    )

    classification_text = module.compose_classification_prompt(policy, issue)
    priority_text = module.compose_priority_prompt(policy, issue)

    assert classification_text.startswith("CLASSIFY PREFIX")
    assert "CUSTOM_REASON_BLOCK" in classification_text
    assert "BUG POLICY:\nBUG_RULE" in classification_text
    assert priority_text.startswith("PRIORITY PREFIX")
    assert "CUSTOM_REASON_BLOCK" in priority_text
    assert "PRIORITY POLICY:\nP_RULE" in priority_text
