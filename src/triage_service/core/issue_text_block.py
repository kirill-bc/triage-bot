"""Plain-text Jira issue fields shared by triage and vision prompts."""

from __future__ import annotations

from triage_service.adapters.jira_issue_fetcher import CommentRef, FetchedIssue


def _select_comments_within_budget(
    comments: list[CommentRef],
    *,
    comments_char_budget: int | None,
) -> list[CommentRef]:
    if not comments:
        return []
    if comments_char_budget is None:
        return comments
    if comments_char_budget <= 0:
        return []
    selected_reversed: list[CommentRef] = []
    used = 0
    for comment in reversed(comments):
        body_len = len(comment.body)
        if body_len == 0:
            selected_reversed.append(comment)
            continue
        if used + body_len > comments_char_budget:
            break
        selected_reversed.append(comment)
        used += body_len
    selected_reversed.reverse()
    return selected_reversed


def _format_comments_section(
    comments: list[CommentRef],
    *,
    comments_char_budget: int | None,
) -> str:
    selected = _select_comments_within_budget(
        comments,
        comments_char_budget=comments_char_budget,
    )
    if not selected:
        return "Comments:\n(none)"
    lines = ["Comments:"]
    for comment in selected:
        author = comment.author or "(unknown)"
        created = comment.created or "(unknown time)"
        body = comment.body or "(attachment-only comment)"
        lines.append(f"- {author} ({created}): {body}")
    return "\n".join(lines)


def format_issue_text_block(
    issue: FetchedIssue,
    *,
    comments_char_budget: int | None = None,
) -> str:
    """Summary, description, reproduction steps, and metadata (no image extraction)."""
    description = issue.description if issue.description is not None else "(none)"
    reproduction_steps = (
        issue.reproduction_steps if issue.reproduction_steps is not None else "(none)"
    )
    priority = issue.priority if issue.priority is not None else "(none)"
    comments_section = _format_comments_section(
        issue.comments,
        comments_char_budget=comments_char_budget,
    )
    return (
        f"Issue key: {issue.issue_key}\n"
        f"Current Jira issue type: {issue.issue_type}\n"
        f"Current Jira priority: {priority}\n"
        f"Reporter: {issue.reporter}\n"
        f"Summary:\n{issue.summary}\n"
        f"Description:\n{description}\n"
        f"Reproduction steps:\n{reproduction_steps}\n"
        f"{comments_section}"
    )
