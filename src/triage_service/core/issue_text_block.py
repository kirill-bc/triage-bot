"""Plain-text Jira issue fields shared by triage and vision prompts."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING

from triage_service.adapters.jira_issue_fetcher import (
    CommentRef,
    FetchedIssue,
    LinkedZendeskTicket,
    ZendeskCommentRef,
    ZendeskResolutionSummary,
    zendesk_comment_newest_first_sort_key,
)
from triage_service.core.zendesk_jira_text_dedupe import (
    sanitize_resolution_summary_against_jira,
)
from triage_service.core.zendesk_summary_dedupe import (
    ZendeskSummaryDedupeState,
    record_rendered_resolution_summary,
    resolution_summary_should_omit_as_duplicate,
)


if TYPE_CHECKING:
    from triage_service.adapters.image_context_extractor import ImageContext


def is_zendesk_image_context(ctx: ImageContext) -> bool:
    """Return True when image context belongs to a linked Zendesk ticket attachment."""
    return _parse_zendesk_ticket_id_from_attachment_id(ctx.attachment_id) is not None


def _normalize_dedupe_key(text: str) -> str:
    return " ".join(text.strip().lower().split())


def _parse_zendesk_ticket_id_from_attachment_id(attachment_id: str) -> str | None:
    if not attachment_id.startswith("zendesk:"):
        return None
    parts = attachment_id.split(":", 2)
    if len(parts) < 3 or not parts[1]:
        return None
    return parts[1]


def _group_zendesk_image_contexts_by_ticket(
    contexts: Sequence[ImageContext],
) -> dict[str, list[ImageContext]]:
    grouped: dict[str, list[ImageContext]] = {}
    for ctx in contexts:
        ticket_id = _parse_zendesk_ticket_id_from_attachment_id(ctx.attachment_id)
        if ticket_id is None:
            continue
        grouped.setdefault(ticket_id, []).append(ctx)
    return grouped


def _format_zendesk_attachment_context(ctx: ImageContext, *, ticket_id: str) -> list[str]:
    source_hint = ctx.source_hint
    if ctx.extraction_failure:
        lines = [
            (
                f"[Zendesk ticket #{ticket_id} attachment: extraction unavailable — "
                f"{ctx.extraction_failure}]"
            ),
        ]
        if source_hint:
            lines.append(f"Source: {source_hint}")
        return lines
    lines = [f"[Zendesk ticket #{ticket_id} attachment: {ctx.filename}]"]
    if source_hint:
        lines.append(f"Source: {source_hint}")
    if ctx.summary:
        lines.append(f"Summary:\n{ctx.summary}")
    return lines


def _format_zendesk_ticket_attachments(
    contexts: Sequence[ImageContext],
    *,
    ticket_id: str,
) -> list[str]:
    lines: list[str] = []
    for ctx in contexts:
        lines.extend(_format_zendesk_attachment_context(ctx, ticket_id=ticket_id))
    return lines


def _format_zendesk_resolution_signals(
    summary: ZendeskResolutionSummary,
    issue: FetchedIssue,
    *,
    seen_hints: set[str],
) -> list[str]:
    summary = sanitize_resolution_summary_against_jira(summary, issue)
    lines = ["Zendesk resolution signals:"]
    lines.append(f"  Initial impact: {summary.initial_impact}")
    lines.append(f"  Latest status: {summary.latest_status}")
    hint_key = _normalize_dedupe_key(summary.resolution_hints)
    if hint_key and hint_key in seen_hints:
        lines.append("  Resolution hints: (same as above)")
    else:
        if hint_key:
            seen_hints.add(hint_key)
        lines.append(f"  Resolution hints: {summary.resolution_hints}")
    lines.append(f"  Open risks: {summary.open_risks}")
    return lines


def _display_timestamp(value: str | None) -> str:
    token = (value or "").strip()
    return token if token else "(none)"


def _format_zendesk_last_activity(comments: Sequence[ZendeskCommentRef]) -> list[str]:
    if not comments:
        return []
    newest = max(comments, key=zendesk_comment_newest_first_sort_key)
    visibility = "public" if newest.public else "internal"
    created = newest.created_at or "(unknown time)"
    return [f"Last activity: {created} ({visibility})"]


def _format_one_zendesk_ticket(
    ticket: LinkedZendeskTicket,
    issue: FetchedIssue,
    *,
    index: int,
    dedupe_state: ZendeskSummaryDedupeState,
    ticket_image_contexts: Sequence[ImageContext] | None = None,
) -> list[str]:
    status = ticket.status or "(none)"
    priority = ticket.priority or "(none)"
    created = _display_timestamp(ticket.created_at)
    header = (
        f"[Zendesk {index}: #{ticket.ticket_id} | status={status} | "
        f"priority={priority} | created={created}]"
    )
    attachment_lines = _format_zendesk_ticket_attachments(
        ticket_image_contexts or (),
        ticket_id=ticket.ticket_id,
    )
    timestamp_lines = _format_zendesk_last_activity(ticket.comments)
    summary = ticket.resolution_summary
    if summary is None:
        lines = [header, f"Subject: {ticket.subject}"]
        if ticket.description:
            lines.append(f"Description:\n{ticket.description}")
        lines.extend(timestamp_lines)
        lines.extend(attachment_lines)
        return lines

    lines = [header, f"Subject: {ticket.subject}"]
    if resolution_summary_should_omit_as_duplicate(ticket, state=dedupe_state):
        lines.append("(resolution signals identical to a prior linked ticket; omitted)")
        lines.extend(timestamp_lines)
        lines.extend(attachment_lines)
        return lines
    record_rendered_resolution_summary(ticket, summary, state=dedupe_state)
    lines.extend(
        _format_zendesk_resolution_signals(
            summary,
            issue,
            seen_hints=dedupe_state.seen_hints,
        ),
    )
    lines.extend(timestamp_lines)
    lines.extend(attachment_lines)
    return lines


def _format_zendesk_tickets(
    issue: FetchedIssue,
    *,
    image_contexts: Sequence[ImageContext] | None = None,
) -> str:
    if not issue.zendesk_tickets:
        return ""
    grouped_contexts = _group_zendesk_image_contexts_by_ticket(image_contexts or ())
    dedupe_state = ZendeskSummaryDedupeState()
    lines = ["Linked Zendesk tickets:"]
    for idx, ticket in enumerate(issue.zendesk_tickets, start=1):
        lines.extend(
            _format_one_zendesk_ticket(
                ticket,
                issue,
                index=idx,
                dedupe_state=dedupe_state,
                ticket_image_contexts=grouped_contexts.get(ticket.ticket_id, ()),
            ),
        )
    return "\n".join(lines)


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
        # Keep a contiguous newest-comment suffix: once an older comment would
        # overflow, stop scanning so we don't skip it and include even older
        # comments. Matches the "drop oldest comments first" budget contract.
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
        if comments_char_budget is not None and comments_char_budget <= 0:
            return "Comments:\n(omitted by comment budget)"
        if comments:
            return "Comments:\n(omitted by comment budget)"
        return "Comments:\n(none)"
    lines = ["Comments:"]
    for comment in selected:
        author = comment.author or "(unknown)"
        created = comment.created or "(unknown time)"
        body = comment.body or "(attachment-only comment)"
        lines.append(f"- {author} ({created}): {body}")
    return "\n".join(lines)


def _utc_today() -> date:
    return datetime.now(timezone.utc).date()


def format_issue_text_block(
    issue: FetchedIssue,
    *,
    comments_char_budget: int | None = None,
    image_contexts: Sequence[ImageContext] | None = None,
    triage_date: date | None = None,
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
    day = triage_date if triage_date is not None else _utc_today()
    created = _display_timestamp(issue.issue_created_at)
    block = (
        f"Triage date (UTC): {day.isoformat()}\n"
        f"Issue key: {issue.issue_key}\n"
        f"Created: {created}\n"
        f"Current Jira issue type: {issue.issue_type}\n"
        f"Current Jira priority: {priority}\n"
        f"Reporter: {issue.reporter}\n"
        f"Summary:\n{issue.summary}\n"
        f"Description:\n{description}\n"
        f"Reproduction steps:\n{reproduction_steps}\n"
        f"{comments_section}"
    )
    zendesk = _format_zendesk_tickets(issue, image_contexts=image_contexts)
    if zendesk:
        return f"{block}\n{zendesk}"
    return block
