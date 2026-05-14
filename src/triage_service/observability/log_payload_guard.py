"""Guardrails for logging large payloads: consistent truncation and explicit markers."""

from __future__ import annotations

from typing import Any

DEFAULT_MAX_LOG_STRING_CHARS = 8192


def _chars_truncated_suffix(total_len: int) -> str:
    return f"… (truncated, {total_len} chars total)"


def truncate_log_string(value: str, *, max_chars: int) -> tuple[str, bool]:
    """Return ``value`` or a prefix plus a fixed truncation marker (character-safe)."""
    if len(value) <= max_chars:
        return value, False
    return value[:max_chars] + _chars_truncated_suffix(len(value)), True


def truncate_logging_value(obj: Any, *, max_string_chars: int) -> tuple[Any, bool]:
    """Deep-copy JSON-like structures (dict/list/str) and truncate long strings."""
    if isinstance(obj, str):
        return truncate_log_string(obj, max_chars=max_string_chars)
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        any_trunc = False
        for key, val in obj.items():
            new_val, child_trunc = truncate_logging_value(val, max_string_chars=max_string_chars)
            out[key] = new_val
            any_trunc = any_trunc or child_trunc
        return out, any_trunc
    if isinstance(obj, list):
        out_list: list[Any] = []
        any_trunc = False
        for item in obj:
            new_item, child_trunc = truncate_logging_value(item, max_string_chars=max_string_chars)
            out_list.append(new_item)
            any_trunc = any_trunc or child_trunc
        return out_list, any_trunc
    return obj, False


def truncate_payload_tree(
    data: dict[str, Any],
    *,
    max_string_chars: int,
) -> tuple[dict[str, Any], bool]:
    """Truncate strings inside an audit-style dict and mark root when anything was clipped."""
    body, any_trunc = truncate_logging_value(data, max_string_chars=max_string_chars)
    if not isinstance(body, dict):
        msg = "truncate_payload_tree expects a dict root"
        raise TypeError(msg)
    out = dict(body)
    if any_trunc:
        out["log_payload_truncated"] = True
    return out, any_trunc


def preview_bytes_for_log(body: bytes, *, max_bytes: int = 8192) -> str:
    """UTF-8 decode with repr fallback; truncate by byte length (same as inbound debug preview)."""
    if len(body) <= max_bytes:
        try:
            return body.decode("utf-8")
        except UnicodeDecodeError:
            return repr(body)
    head = body[:max_bytes]
    try:
        fragment = head.decode("utf-8")
    except UnicodeDecodeError:
        fragment = repr(head)
    return f"{fragment}… (truncated, {len(body)} bytes total)"
