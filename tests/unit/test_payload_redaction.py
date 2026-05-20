"""Unit tests for model payload redaction helpers."""

from __future__ import annotations

from typing import Any

import pytest

from triage_service.observability.payload_redaction import (
    sanitize_chat_messages,
    sanitize_model_output_text,
    sanitize_vision_messages,
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


@pytest.mark.unit
def test_sanitize_vision_messages_noop_when_redact_false() -> None:
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": "vision system"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "describe screenshot"},
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64,QUJDRA=="},
                },
            ],
        },
    ]
    out = sanitize_vision_messages(messages, redact=False)
    assert out[1]["content"][1]["image_url"]["url"] == "data:image/png;base64,QUJDRA=="


@pytest.mark.unit
def test_sanitize_vision_messages_redacts_image_data_urls_when_redact_true() -> None:
    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64,QUJDRA=="},
                },
            ],
        },
    ]
    out = sanitize_vision_messages(messages, redact=True)
    assert "base64_len=8" in out[0]["content"][0]["image_url"]["url"]
