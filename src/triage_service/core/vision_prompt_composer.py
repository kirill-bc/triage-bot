"""Compose vision preprocessor prompts (Langfuse-first, JSON fallback)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TypedDict

from triage_service.adapters.jira_issue_fetcher import FetchedIssue
from triage_service.core.langfuse_prompt_config import (
    fetch_langfuse_text_prompt,
    vision_system_prompt_name,
    vision_user_prompt_name,
)
from triage_service.core.issue_text_block import format_issue_text_block


class _VisionPromptTemplates(TypedDict):
    vision_system_prompt: str
    vision_user_instruction: str


def _resolve_template_path() -> Path:
    configured_path = os.getenv("TRIAGE_PROMPT_TEMPLATES_PATH")
    if configured_path:
        return Path(configured_path)
    return Path(__file__).parent / "prompt_templates.json"


def _load_vision_prompt_templates() -> _VisionPromptTemplates:
    template_path = _resolve_template_path()
    payload = json.loads(template_path.read_text(encoding="utf-8"))
    required_keys = ("vision_system_prompt", "vision_user_instruction")
    for key in required_keys:
        value = payload.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError(f"Prompt template key '{key}' must be a non-empty string.")
    return {
        "vision_system_prompt": payload["vision_system_prompt"],
        "vision_user_instruction": payload["vision_user_instruction"],
    }


_VISION_PROMPT_TEMPLATES = _load_vision_prompt_templates()


def format_vision_issue_context(issue: FetchedIssue) -> str:
    """Ticket text passed to vision preprocessing (same fields as classification issue block)."""
    return format_issue_text_block(issue)


def compose_vision_system_prompt() -> str:
    """System prompt for OpenRouter vision attachment transcription."""
    langfuse_text = fetch_langfuse_text_prompt(vision_system_prompt_name())
    if langfuse_text is not None:
        return langfuse_text
    return _VISION_PROMPT_TEMPLATES["vision_system_prompt"]


def compose_vision_user_instruction(issue: FetchedIssue) -> str:
    """User text with ticket context plus TRANSCRIPT / SUMMARY format (image sent separately)."""
    issue_block = format_vision_issue_context(issue)
    langfuse_text = fetch_langfuse_text_prompt(
        vision_user_prompt_name(),
        issue_block=issue_block,
    )
    if langfuse_text is not None:
        return langfuse_text
    return _VISION_PROMPT_TEMPLATES["vision_user_instruction"].format(
        issue_block=issue_block,
    )
