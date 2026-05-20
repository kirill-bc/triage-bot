"""Langfuse text-prompt fetch helper (settings-driven; no direct os.getenv)."""

from __future__ import annotations

import logging

from langfuse import get_client

from triage_service.core.settings import AppSettings

LOGGER = logging.getLogger(__name__)


def fetch_langfuse_text_prompt(
    settings: AppSettings,
    prompt_name: str,
    **compile_kwargs: str,
) -> str | None:
    """Fetch and compile a Langfuse text prompt; return None on disable or failure."""
    if not settings.langfuse_prompt_management_enabled:
        return None
    try:
        prompt = get_client().get_prompt(
            prompt_name,
            label=settings.triage_langfuse_prompt_label,
            type="text",
            cache_ttl_seconds=settings.triage_langfuse_prompt_cache_ttl_seconds,
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
