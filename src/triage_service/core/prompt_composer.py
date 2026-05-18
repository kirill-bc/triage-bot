"""Compose model inputs for sequential triage.

Classification uses bug policy only; priority uses priority policy only. Step (1) omits priority
policy text; step (2) omits bug definition. User prompts frame **TriageBot** (calm internal-support
tone) and how ``reason`` may appear in Jira. Orchestration picks which composer to call; policies
are not merged into one always-on prompt.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TypedDict

from triage_service.adapters.jira_issue_fetcher import FetchedIssue
from triage_service.core.policy_context import PolicyContext


class _PromptTemplates(TypedDict):
    reason_for_humans: str
    classification_template: str
    priority_template: str


def _resolve_template_path() -> Path:
    configured_path = os.getenv("TRIAGE_PROMPT_TEMPLATES_PATH")
    if configured_path:
        return Path(configured_path)
    return Path(__file__).with_name("prompt_templates.json")


def _load_prompt_templates() -> _PromptTemplates:
    template_path = _resolve_template_path()
    payload = json.loads(template_path.read_text(encoding="utf-8"))
    required_keys = ("reason_for_humans", "classification_template", "priority_template")
    for key in required_keys:
        value = payload.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError(f"Prompt template key '{key}' must be a non-empty string.")
    return {
        "reason_for_humans": payload["reason_for_humans"],
        "classification_template": payload["classification_template"],
        "priority_template": payload["priority_template"],
    }


_PROMPT_TEMPLATES = _load_prompt_templates()


def _issue_block(issue: FetchedIssue) -> str:
    description = issue.description if issue.description is not None else "(none)"
    reproduction_steps = (
        issue.reproduction_steps if issue.reproduction_steps is not None else "(none)"
    )
    priority = issue.priority if issue.priority is not None else "(none)"
    return (
        f"Issue key: {issue.issue_key}\n"
        f"Current Jira issue type: {issue.issue_type}\n"
        f"Current Jira priority: {priority}\n"
        f"Reporter: {issue.reporter}\n"
        f"Summary:\n{issue.summary}\n"
        f"Description:\n{description}\n"
        f"Reproduction steps:\n{reproduction_steps}"
    )


def compose_classification_prompt(policy: PolicyContext, issue: FetchedIssue) -> str:
    """User/model input for Story vs Bug classification using bug definition only."""
    return _PROMPT_TEMPLATES["classification_template"].format(
        reason_for_humans=_PROMPT_TEMPLATES["reason_for_humans"],
        bug_definition=policy.bug_definition,
        issue_block=_issue_block(issue),
    )


def compose_priority_prompt(policy: PolicyContext, issue: FetchedIssue) -> str:
    """P0–P4 input using priority definition only (Bug path, after classification)."""
    return _PROMPT_TEMPLATES["priority_template"].format(
        reason_for_humans=_PROMPT_TEMPLATES["reason_for_humans"],
        priority_definition=policy.priority_definition,
        issue_block=_issue_block(issue),
    )
