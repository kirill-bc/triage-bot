"""Plain-text Jira issue fields shared by triage and vision prompts."""

from __future__ import annotations

from triage_service.adapters.jira_issue_fetcher import FetchedIssue


def _format_zendesk_tickets(issue: FetchedIssue) -> str:
    if not issue.zendesk_tickets:
        return ""
    lines = ["Linked Zendesk tickets:"]
    for idx, ticket in enumerate(issue.zendesk_tickets, start=1):
        status = ticket.status or "(none)"
        priority = ticket.priority or "(none)"
        lines.append(
            f"[Zendesk {idx}: #{ticket.ticket_id} | status={status} | priority={priority}]",
        )
        lines.append(f"Subject: {ticket.subject}")
        if ticket.description:
            lines.append(f"Description:\n{ticket.description}")
    return "\n".join(lines)


def format_issue_text_block(issue: FetchedIssue) -> str:
    """Summary, description, reproduction steps, and metadata (no image extraction)."""
    description = issue.description if issue.description is not None else "(none)"
    reproduction_steps = (
        issue.reproduction_steps if issue.reproduction_steps is not None else "(none)"
    )
    priority = issue.priority if issue.priority is not None else "(none)"
    block = (
        f"Issue key: {issue.issue_key}\n"
        f"Current Jira issue type: {issue.issue_type}\n"
        f"Current Jira priority: {priority}\n"
        f"Reporter: {issue.reporter}\n"
        f"Summary:\n{issue.summary}\n"
        f"Description:\n{description}\n"
        f"Reproduction steps:\n{reproduction_steps}"
    )
    zendesk = _format_zendesk_tickets(issue)
    if zendesk:
        return f"{block}\n{zendesk}"
    return block
