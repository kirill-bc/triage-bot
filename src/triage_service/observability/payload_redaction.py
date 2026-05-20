"""Centralized redaction for model payloads sent to Langfuse and audit sinks."""

from __future__ import annotations

from typing import Any

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


def _redact_vision_image_url(url: str) -> dict[str, Any]:
    """Replace image URLs with a compact summary (never ship base64 blobs to Langfuse)."""
    if url.startswith("data:") and ";base64," in url:
        header, _, payload = url.partition(";base64,")
        mime = header.removeprefix("data:") or "unknown"
        return {
            "type": "image_url",
            "image_url": {
                "url": (
                    f"{_REDACTED_PLACEHOLDER} image mime={mime} "
                    f"base64_len={len(payload)}"
                ),
            },
        }
    return {
        "type": "image_url",
        "image_url": {"url": f"{_REDACTED_PLACEHOLDER} image url_len={len(url)}"},
    }


def _redact_vision_content_part(part: dict[str, Any], *, redact: bool) -> dict[str, Any]:
    part_type = str(part.get("type", ""))
    if part_type == "text":
        text = str(part.get("text", ""))
        if not redact:
            return {"type": "text", "text": text}
        return {"type": "text", "text": _redacted_summary(text)}
    if part_type == "image_url":
        image_url = part.get("image_url")
        url = ""
        if isinstance(image_url, dict):
            url = str(image_url.get("url", ""))
        if not redact:
            return dict(part)
        return _redact_vision_image_url(url)
    return dict(part)


def sanitize_vision_messages(
    messages: list[dict[str, Any]],
    *,
    redact: bool,
) -> list[dict[str, Any]]:
    """Return multimodal chat messages with text and image payloads redacted when enabled."""
    if not redact:
        return [dict(msg) for msg in messages]
    out: list[dict[str, Any]] = []
    for msg in messages:
        role = str(msg.get("role", ""))
        content = msg.get("content")
        if role == "system" and isinstance(content, str):
            out.append({"role": role, "content": content})
            continue
        if isinstance(content, list):
            parts = [
                _redact_vision_content_part(part, redact=redact)
                for part in content
                if isinstance(part, dict)
            ]
            out.append({"role": role, "content": parts})
            continue
        if isinstance(content, str):
            out.append({"role": role, "content": _redacted_summary(content)})
            continue
        out.append(dict(msg))
    return out
