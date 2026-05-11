"""OpenRouter chat completions client (OpenAI-compatible API)."""

from __future__ import annotations

from typing import Any

import httpx

from settings import AppSettings

OPENROUTER_CHAT_COMPLETIONS_URL = "https://openrouter.ai/api/v1/chat/completions"


class OpenRouterInferenceError(RuntimeError):
    """Raised when OpenRouter returns an error or an unusable completion payload."""


class OpenRouterInferenceClient:
    """POSTs chat completions using the model id from application settings."""

    def __init__(self, settings: AppSettings, *, client: httpx.Client | None = None) -> None:
        self._settings = settings
        self._client = client

    def chat_completion(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> str:
        headers = {
            "Authorization": f"Bearer {self._settings.openrouter_api_key}",
            "Content-Type": "application/json",
        }
        body: dict[str, Any] = {
            "model": self._settings.openrouter_model,
            "messages": list(messages),
            "temperature": temperature,
        }
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        if self._client is not None:
            return self._post(self._client, body, headers)

        with httpx.Client(timeout=60.0) as client:
            return self._post(client, body, headers)

    def _post(self, client: httpx.Client, body: dict[str, Any], headers: dict[str, str]) -> str:
        response = client.post(OPENROUTER_CHAT_COMPLETIONS_URL, headers=headers, json=body)
        if response.is_error:
            snippet = response.text[:200]
            raise OpenRouterInferenceError(
                f"OpenRouter request failed with HTTP {response.status_code}: {snippet}",
            )
        payload = response.json()
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise OpenRouterInferenceError("OpenRouter response missing choices.")
        first = choices[0]
        if not isinstance(first, dict):
            raise OpenRouterInferenceError("OpenRouter response has invalid choice shape.")
        message = first.get("message")
        if not isinstance(message, dict):
            raise OpenRouterInferenceError("OpenRouter response missing message object.")
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise OpenRouterInferenceError(
                "OpenRouter response missing non-empty assistant content.",
            )
        return content
