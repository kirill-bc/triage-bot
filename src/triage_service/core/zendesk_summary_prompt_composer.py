"""Compose Zendesk comment summarization prompts (Langfuse-first, JSON fallback)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TypedDict

from triage_service.adapters.jira_issue_fetcher import (
    FetchedIssue,
    LinkedZendeskTicket,
    ZendeskCommentRef,
)
from triage_service.core.langfuse_prompt_config import fetch_langfuse_text_prompt
from triage_service.core.settings import AppSettings


class _ZendeskSummaryPromptTemplates(TypedDict):
    zendesk_summary_system_prompt: str
    zendesk_summary_user_instruction: str


def _resolve_template_path() -> Path:
    configured_path = os.getenv("TRIAGE_PROMPT_TEMPLATES_PATH")
    if configured_path:
        return Path(configured_path)
    return Path(__file__).parent / "prompt_templates.json"


def _load_zendesk_summary_prompt_templates() -> _ZendeskSummaryPromptTemplates:
    template_path = _resolve_template_path()
    payload = json.loads(template_path.read_text(encoding="utf-8"))
    required_keys = ("zendesk_summary_system_prompt", "zendesk_summary_user_instruction")
    for key in required_keys:
        value = payload.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError(f"Prompt template key '{key}' must be a non-empty string.")
    return {
        "zendesk_summary_system_prompt": payload["zendesk_summary_system_prompt"],
        "zendesk_summary_user_instruction": payload["zendesk_summary_user_instruction"],
    }


_ZENDESK_SUMMARY_PROMPT_TEMPLATES = _load_zendesk_summary_prompt_templates()


def format_jira_context_for_zendesk_summary(issue: FetchedIssue) -> str:
    """Jira fields passed to Zendesk summarization (excludes linked Zendesk tickets)."""
    description = issue.description if issue.description is not None else "(none)"
    reproduction_steps = (
        issue.reproduction_steps if issue.reproduction_steps is not None else "(none)"
    )
    return (
        f"Issue key: {issue.issue_key}\n"
        f"Summary:\n{issue.summary}\n"
        f"Description:\n{description}\n"
        f"Reproduction steps:\n{reproduction_steps}"
    )


def format_zendesk_comments_for_summary(comments: list[ZendeskCommentRef]) -> str:
    if not comments:
        return "(none)"
    lines: list[str] = []
    for comment in comments:
        visibility = "public" if comment.public else "internal"
        created = comment.created_at or "(unknown time)"
        body = comment.body or "(empty)"
        lines.append(f"- [{visibility}] {created}: {body}")
    return "\n".join(lines)


def compose_zendesk_summary_system_prompt(*, settings: AppSettings) -> str:
    langfuse_text = fetch_langfuse_text_prompt(
        settings,
        settings.triage_langfuse_zendesk_summary_system_prompt_name,
    )
    if langfuse_text is not None:
        return langfuse_text
    return _ZENDESK_SUMMARY_PROMPT_TEMPLATES["zendesk_summary_system_prompt"]


def compose_zendesk_summary_user_instruction(
    issue: FetchedIssue,
    ticket: LinkedZendeskTicket,
    comments: list[ZendeskCommentRef],
    *,
    settings: AppSettings,
) -> str:
    jira_context = format_jira_context_for_zendesk_summary(issue)
    ticket_comments = format_zendesk_comments_for_summary(comments)
    langfuse_text = fetch_langfuse_text_prompt(
        settings,
        settings.triage_langfuse_zendesk_summary_user_prompt_name,
        jira_context=jira_context,
        ticket_id=ticket.ticket_id,
        ticket_subject=ticket.subject,
        ticket_status=ticket.status or "(none)",
        ticket_priority=ticket.priority or "(none)",
        ticket_description=ticket.description or "(none)",
        ticket_comments=ticket_comments,
    )
    if langfuse_text is not None:
        return langfuse_text
    return _ZENDESK_SUMMARY_PROMPT_TEMPLATES["zendesk_summary_user_instruction"].format(
        jira_context=jira_context,
        ticket_id=ticket.ticket_id,
        ticket_subject=ticket.subject,
        ticket_status=ticket.status or "(none)",
        ticket_priority=ticket.priority or "(none)",
        ticket_description=ticket.description or "(none)",
        ticket_comments=ticket_comments,
    )
