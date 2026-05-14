"""Unit tests for model payload redaction helpers."""

from __future__ import annotations

import pytest

from triage_service.observability.payload_redaction import (
    sanitize_chat_messages,
    sanitize_model_output_text,
)


@pytest.mark.unit
def test_sanitize_chat_messages_noop_when_redact_false() -> None:
    msgs = [{"role": "user", "content": "secret"}]
    out = sanitize_chat_messages(msgs, redact=False)
    assert out == msgs
    assert out is not msgs


@pytest.mark.unit
def test_sanitize_chat_messages_redacts_content_preserves_roles() -> None:
    msgs = [
        {"role": "system", "content": "sys-secret"},
        {"role": "user", "content": "user-secret"},
    ]
    out = sanitize_chat_messages(msgs, redact=True)
    assert out[0] == {"role": "system", "content": "sys-secret"}
    assert out[1]["role"] == "user"
    assert out[1]["content"].startswith("[REDACTED] len=")
    assert 'preview="user-secret"' in out[1]["content"]


@pytest.mark.unit
def test_sanitize_model_output_text_redacts_when_requested() -> None:
    assert sanitize_model_output_text("raw-json", redact=False) == "raw-json"
    redacted = sanitize_model_output_text("raw-json", redact=True)
    assert redacted.startswith("[REDACTED] len=")
    assert 'preview="raw-json"' in redacted
