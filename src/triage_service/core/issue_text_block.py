"""Plain-text Jira issue fields shared by triage and vision prompts."""

from __future__ import annotations

from triage_service.adapters.jira_issue_fetcher import FetchedIssue


def format_issue_text_block(issue: FetchedIssue) -> str:
    """Summary, description, reproduction steps, and metadata (no image extraction)."""
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
