"""Centralized redaction for model payloads sent to Langfuse and audit sinks."""

from __future__ import annotations

_REDACTED_PLACEHOLDER = "[REDACTED]"
_PREVIEW_CHARS = 64


def _redacted_summary(text: str) -> str:
    """Return a compact redaction envelope with size + preview for debugging."""
    trimmed = text.strip()
    if not trimmed:
        return f"{_REDACTED_PLACEHOLDER} len=0"
    preview = trimmed[:_PREVIEW_CHARS]
    suffix = "..." if len(trimmed) > _PREVIEW_CHARS else ""
    return f'{_REDACTED_PLACEHOLDER} len={len(trimmed)} preview="{preview}{suffix}"'


def sanitize_chat_messages(
    messages: list[dict[str, str]],
    *,
    redact: bool,
) -> list[dict[str, str]]:
    """Return chat messages with role-aware redaction.

    ``system`` content is kept intact to preserve prompt-debug value.
    Other roles are masked to a summary envelope.
    """
    if not redact:
        return list(messages)
    out: list[dict[str, str]] = []
    for msg in messages:
        role = str(msg.get("role", ""))
        content = str(msg.get("content", ""))
        if role == "system":
            out.append({"role": role, "content": content})
            continue
        out.append({"role": role, "content": _redacted_summary(content)})
    return out


def sanitize_model_output_text(text: str, *, redact: bool) -> str:
    """Return assistant text or a summarized redaction envelope."""
    if not redact:
        return text
    return _redacted_summary(text)
