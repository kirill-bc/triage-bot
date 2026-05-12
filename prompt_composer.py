"""Compose model inputs for sequential triage.

Classification uses bug policy only; priority uses priority policy only. Step (1) omits priority
policy text; step (2) omits bug definition. User prompts frame **TriageBot** (calm internal-support
tone) and how ``reason`` may appear in Jira. Orchestration picks which composer to call; policies
are not merged into one always-on prompt.
"""

from __future__ import annotations

from jira_issue_fetcher import FetchedIssue
from policy_context import PolicyContext

_REASON_FOR_HUMANS = (
    "In the JSON field `reason`, write one to three short sentences a teammate can skim quickly. "
    "They may appear verbatim in Jira after a short automated prefix, so write like good internal "
    "support: factual, plain language, enough to act on—no flattery, apology loops, stiff "
    "warnings, or lecturing. Do not reference prompts, JSON fields, or \"the model\".\n\n"
)


def _issue_block(issue: FetchedIssue) -> str:
    description = issue.description if issue.description is not None else "(none)"
    priority = issue.priority if issue.priority is not None else "(none)"
    return (
        f"Issue key: {issue.issue_key}\n"
        f"Current Jira issue type: {issue.issue_type}\n"
        f"Current Jira priority: {priority}\n"
        f"Reporter: {issue.reporter}\n"
        f"Summary:\n{issue.summary}\n"
        f"Description:\n{description}"
    )


def compose_classification_prompt(policy: PolicyContext, issue: FetchedIssue) -> str:
    """User/model input for Story vs Bug classification using bug definition only."""
    return (
        "## Role\n"
        "You are **TriageBot**, automated Jira triage in a \"friendly neighborhood\" spirit: the "
        "same steady, approachable voice as seasoned internal support—clear, factual, respectful. "
        "Cite briefly what in the issue text supports the label. Skip small talk, slogans, and "
        "pushy or punitive phrasing.\n\n"
        "## Task\n"
        "Classify this Jira issue as Bug or Story using ONLY the bug definition that follows. "
        "Do not assign or discuss P0–P4 priority in this step; if the outcome is Bug, priority is "
        "inferred in a separate step with a different policy excerpt.\n\n"
        f"{_REASON_FOR_HUMANS}"
        "## Bug definition (policy)\n"
        f"{policy.bug_definition}\n\n"
        "## Issue (current Jira state)\n"
        f"{_issue_block(issue)}\n"
    )


def compose_priority_prompt(policy: PolicyContext, issue: FetchedIssue) -> str:
    """P0–P4 input using priority definition only (Bug path, after classification)."""
    return (
        "## Role\n"
        "You are still **TriageBot**, on the Bug path's severity step only. Same support-desk "
        "discipline: concise, map impact to exactly one P0–P4 label, no scolding.\n\n"
        "## Task\n"
        "The issue is being triaged on the Bug path. Using ONLY the priority definition "
        "that follows, recommend exactly one label from P0, P1, P2, P3, or P4.\n\n"
        f"{_REASON_FOR_HUMANS}"
        "## Priority definition (policy)\n"
        f"{policy.priority_definition}\n\n"
        "## Issue (current Jira state)\n"
        f"{_issue_block(issue)}\n"
    )
