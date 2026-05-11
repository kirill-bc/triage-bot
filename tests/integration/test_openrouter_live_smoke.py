"""Optional live OpenRouter call (network + billed usage).

Skipped unless ``OPENROUTER_LIVE_SMOKE`` is truthy. CI and default ``pytest -m integration``
runs stay offline; enable locally when you want to verify credentials and routing.
"""

from __future__ import annotations

import os

import pytest

from openrouter_inference_client import OpenRouterInferenceClient
from settings import load_settings


def _truthy_env(name: str) -> bool:
    raw = os.environ.get(name, "")
    return raw.strip().lower() in ("1", "true", "yes")


@pytest.mark.integration
@pytest.mark.skipif(
    not _truthy_env("OPENROUTER_LIVE_SMOKE"),
    reason="Set OPENROUTER_LIVE_SMOKE=1 to run (uses network and OpenRouter credits).",
)
def test_openrouter_live_chat_completion_smoke() -> None:
    settings = load_settings()
    client = OpenRouterInferenceClient(settings)
    # Reasoning-style models may spend budget in `reasoning` before `content`; keep headroom.
    text = client.chat_completion(
        messages=[
            {
                "role": "user",
                "content": "Reply with the single word pong and nothing else.",
            },
        ],
        temperature=0,
        max_tokens=256,
    )
    normalized = text.strip().lower()
    assert normalized
    assert len(text) < 500
    assert "pong" in normalized
