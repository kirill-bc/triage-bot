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
from collections.abc import Sequence
from typing import TypedDict

from triage_service.adapters.image_context_extractor import ImageContext
from triage_service.adapters.jira_issue_fetcher import FetchedIssue
from triage_service.core.issue_text_block import format_issue_text_block
from triage_service.core.langfuse_prompt_config import fetch_langfuse_text_prompt
from triage_service.core.policy_context import PolicyContext
from triage_service.core.settings import AppSettings


class _PromptTemplates(TypedDict):
    reason_for_humans: str
    classification_system_prompt: str
    priority_system_prompt: str
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
    required_keys = (
        "reason_for_humans",
        "classification_system_prompt",
        "priority_system_prompt",
        "classification_template",
        "priority_template",
    )
    for key in required_keys:
        value = payload.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError(f"Prompt template key '{key}' must be a non-empty string.")
    return {
        "reason_for_humans": payload["reason_for_humans"],
        "classification_system_prompt": payload["classification_system_prompt"],
        "priority_system_prompt": payload["priority_system_prompt"],
        "classification_template": payload["classification_template"],
        "priority_template": payload["priority_template"],
    }


_PROMPT_TEMPLATES = _load_prompt_templates()


def _reason_for_humans_text(settings: AppSettings) -> str:
    langfuse_text = fetch_langfuse_text_prompt(
        settings,
        settings.triage_langfuse_reason_for_humans_prompt_name,
    )
    if langfuse_text is not None:
        return langfuse_text
    return _PROMPT_TEMPLATES["reason_for_humans"]


def compose_classification_system_prompt(*, settings: AppSettings) -> str:
    """System prompt for the classification inference step."""
    langfuse_text = fetch_langfuse_text_prompt(
        settings,
        settings.triage_langfuse_classification_system_prompt_name,
    )
    if langfuse_text is not None:
        return langfuse_text
    return _PROMPT_TEMPLATES["classification_system_prompt"]


def compose_priority_system_prompt(*, settings: AppSettings) -> str:
    """System prompt for the priority inference step."""
    langfuse_text = fetch_langfuse_text_prompt(
        settings,
        settings.triage_langfuse_priority_system_prompt_name,
    )
    if langfuse_text is not None:
        return langfuse_text
    return _PROMPT_TEMPLATES["priority_system_prompt"]


def _format_attached_images_section(contexts: Sequence[ImageContext]) -> str:
    if not contexts:
        return ""
    lines = ["Attached images:"]
    for index, ctx in enumerate(contexts, start=1):
        if ctx.extraction_failure:
            lines.append(
                f"[Attachment {index}: extraction unavailable — {ctx.extraction_failure}]",
            )
            continue
        lines.append(f"[Attachment {index}: {ctx.filename}]")
        if ctx.summary:
            lines.append(f"Summary:\n{ctx.summary}")
    return "\n".join(lines)


def _issue_block(
    issue: FetchedIssue,
    *,
    settings: AppSettings | None = None,
    image_contexts: Sequence[ImageContext] | None = None,
) -> str:
    comments_char_budget = (
        settings.triage_comments_char_budget if settings is not None else None
    )
    base = format_issue_text_block(issue, comments_char_budget=comments_char_budget)
    images_section = _format_attached_images_section(image_contexts or ())
    if not images_section:
        return base
    return f"{base}\n{images_section}"


def compose_classification_prompt(
    policy: PolicyContext,
    issue: FetchedIssue,
    *,
    settings: AppSettings,
    image_contexts: Sequence[ImageContext] | None = None,
) -> str:
    """User/model input for Story vs Bug classification.

    Langfuse ``classification-user`` embeds policy and reason guidance; only ``issue_block`` is
    compiled in. Local fallback stitches bug policy and reason text from ``policy`` and templates.
    """
    issue_block = _issue_block(
        issue,
        settings=settings,
        image_contexts=image_contexts,
    )
    langfuse_prompt = fetch_langfuse_text_prompt(
        settings,
        settings.triage_langfuse_classification_prompt_name,
        issue_block=issue_block,
    )
    if langfuse_prompt is not None:
        return langfuse_prompt
    return _PROMPT_TEMPLATES["classification_template"].format(
        reason_for_humans=_reason_for_humans_text(settings),
        bug_definition=policy.bug_definition,
        issue_block=issue_block,
    )


def compose_priority_prompt(
    policy: PolicyContext,
    issue: FetchedIssue,
    *,
    settings: AppSettings,
    image_contexts: Sequence[ImageContext] | None = None,
) -> str:
    """P0–P4 user input on the Bug path (after classification).

    Langfuse ``priority-user`` embeds policy and reason guidance; only ``issue_block`` is compiled
    in. Local fallback stitches priority policy and reason text from ``policy`` and templates.
    """
    issue_block = _issue_block(
        issue,
        settings=settings,
        image_contexts=image_contexts,
    )
    langfuse_prompt = fetch_langfuse_text_prompt(
        settings,
        settings.triage_langfuse_priority_prompt_name,
        issue_block=issue_block,
    )
    if langfuse_prompt is not None:
        return langfuse_prompt
    return _PROMPT_TEMPLATES["priority_template"].format(
        reason_for_humans=_reason_for_humans_text(settings),
        priority_definition=policy.priority_definition,
        issue_block=issue_block,
    )
