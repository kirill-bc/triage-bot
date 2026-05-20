"""Shared Langfuse prompt-management settings and text-prompt fetch helpers."""

from __future__ import annotations

import logging
import os

from langfuse import get_client

LOGGER = logging.getLogger(__name__)

# Langfuse project "triagebot" — names match prompts in the UI (triagebot/<name>).
DEFAULT_REASON_FOR_HUMANS_PROMPT_NAME = "triagebot/reason-for-humans"
DEFAULT_CLASSIFICATION_SYSTEM_PROMPT_NAME = "triagebot/classification-system"
DEFAULT_PRIORITY_SYSTEM_PROMPT_NAME = "triagebot/priority-system"
DEFAULT_CLASSIFICATION_USER_PROMPT_NAME = "triagebot/classification-user"
DEFAULT_PRIORITY_USER_PROMPT_NAME = "triagebot/priority-user"
DEFAULT_VISION_SYSTEM_PROMPT_NAME = "triagebot/vision-system"
DEFAULT_VISION_USER_PROMPT_NAME = "triagebot/vision-user"


def _prompt_name_from_env(env_var: str, default: str) -> str:
    configured = os.getenv(env_var, "").strip()
    return configured or default


def reason_for_humans_prompt_name() -> str:
    return _prompt_name_from_env(
        "TRIAGE_LANGFUSE_REASON_FOR_HUMANS_PROMPT_NAME",
        DEFAULT_REASON_FOR_HUMANS_PROMPT_NAME,
    )


def classification_system_prompt_name() -> str:
    return _prompt_name_from_env(
        "TRIAGE_LANGFUSE_CLASSIFICATION_SYSTEM_PROMPT_NAME",
        DEFAULT_CLASSIFICATION_SYSTEM_PROMPT_NAME,
    )


def priority_system_prompt_name() -> str:
    return _prompt_name_from_env(
        "TRIAGE_LANGFUSE_PRIORITY_SYSTEM_PROMPT_NAME",
        DEFAULT_PRIORITY_SYSTEM_PROMPT_NAME,
    )


def classification_user_prompt_name() -> str:
    return _prompt_name_from_env(
        "TRIAGE_LANGFUSE_CLASSIFICATION_PROMPT_NAME",
        DEFAULT_CLASSIFICATION_USER_PROMPT_NAME,
    )


def priority_user_prompt_name() -> str:
    return _prompt_name_from_env(
        "TRIAGE_LANGFUSE_PRIORITY_PROMPT_NAME",
        DEFAULT_PRIORITY_USER_PROMPT_NAME,
    )


def vision_system_prompt_name() -> str:
    return _prompt_name_from_env(
        "TRIAGE_LANGFUSE_VISION_SYSTEM_PROMPT_NAME",
        DEFAULT_VISION_SYSTEM_PROMPT_NAME,
    )


def vision_user_prompt_name() -> str:
    return _prompt_name_from_env(
        "TRIAGE_LANGFUSE_VISION_USER_PROMPT_NAME",
        DEFAULT_VISION_USER_PROMPT_NAME,
    )


def langfuse_prompts_enabled() -> bool:
    """True when Langfuse prompt management is on and API keys are present."""
    enabled_token = os.getenv("TRIAGE_LANGFUSE_PROMPTS_ENABLED", "true").strip().lower()
    if enabled_token in ("0", "false", "no", "off"):
        return False
    return bool(os.getenv("LANGFUSE_PUBLIC_KEY", "").strip()) and bool(
        os.getenv("LANGFUSE_SECRET_KEY", "").strip(),
    )


def langfuse_prompt_label() -> str | None:
    label = os.getenv("TRIAGE_LANGFUSE_PROMPT_LABEL", "").strip()
    return label or None


def langfuse_prompt_cache_ttl_seconds() -> int | None:
    value = os.getenv("TRIAGE_LANGFUSE_PROMPT_CACHE_TTL_SECONDS")
    if value is None:
        return None
    token = value.strip()
    if not token:
        return None
    try:
        return int(token)
    except ValueError:
        LOGGER.warning(
            "Invalid TRIAGE_LANGFUSE_PROMPT_CACHE_TTL_SECONDS; defaulting to SDK default",
        )
        return None


def fetch_langfuse_text_prompt(
    prompt_name: str,
    **compile_kwargs: str,
) -> str | None:
    """Fetch and compile a Langfuse text prompt; return None on disable or failure."""
    if not langfuse_prompts_enabled():
        return None
    try:
        prompt = get_client().get_prompt(
            prompt_name,
            label=langfuse_prompt_label(),
            type="text",
            cache_ttl_seconds=langfuse_prompt_cache_ttl_seconds(),
        )
        compiled = prompt.compile(**compile_kwargs)
        return compiled if isinstance(compiled, str) and compiled.strip() else None
    except Exception:
        LOGGER.warning(
            "Langfuse text prompt fetch/compile failed",
            extra={"prompt_name": prompt_name},
            exc_info=True,
        )
        return None
