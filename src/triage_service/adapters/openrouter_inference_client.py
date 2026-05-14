"""OpenRouter chat completions client (OpenAI-compatible API)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from triage_service.core.settings import AppSettings

OPENROUTER_CHAT_COMPLETIONS_URL = "https://openrouter.ai/api/v1/chat/completions"


class OpenRouterInferenceError(RuntimeError):
    """Raised when OpenRouter returns an error or an unusable completion payload."""


@dataclass(frozen=True)
class OpenRouterCompletionResult:
    """Assistant text plus optional provider usage/cost telemetry."""

    content: str
    usage_details: dict[str, int] | None = None
    cost_details: dict[str, float] | None = None


class OpenRouterInferenceClient:
    """POSTs chat completions using the model id from application settings."""

    def __init__(
        self,
        settings: AppSettings,
        *,
        client: httpx.Client | None = None,
        model_override: str | None = None,
    ) -> None:
        self._settings = settings
        self._client = client
        stripped = model_override.strip() if model_override else ""
        self._model_override = stripped or None

    @property
    def effective_model_id(self) -> str:
        """Resolved OpenRouter model id (override or configured default)."""
        return self._model_override or self._settings.openrouter_model

    def chat_completion(
        self,
        messages: list[dict[str, str]],
        *,
        run_id: str,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> str:
        return self.chat_completion_with_details(
            messages,
            run_id=run_id,
            temperature=temperature,
            max_tokens=max_tokens,
        ).content

    def chat_completion_with_details(
        self,
        messages: list[dict[str, str]],
        *,
        run_id: str,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> OpenRouterCompletionResult:
        _ = run_id
        headers = {
            "Authorization": f"Bearer {self._settings.openrouter_api_key}",
            "Content-Type": "application/json",
        }
        model_id = self._model_override or self._settings.openrouter_model
        body: dict[str, Any] = {
            "model": model_id,
            "messages": list(messages),
            "temperature": temperature,
        }
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        if self._client is not None:
            return self._post(self._client, body, headers)

        with httpx.Client(timeout=60.0) as client:
            return self._post(client, body, headers)

    def _post(
        self,
        client: httpx.Client,
        body: dict[str, Any],
        headers: dict[str, str],
    ) -> OpenRouterCompletionResult:
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
        usage_details = _extract_usage_details(payload)
        cost_details = _extract_cost_details(payload)
        return OpenRouterCompletionResult(
            content=content,
            usage_details=usage_details,
            cost_details=cost_details,
        )


def _extract_usage_details(payload: dict[str, Any]) -> dict[str, int] | None:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None
    details: dict[str, int] = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = usage.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            details[key] = value
            continue
        if isinstance(value, float) and value.is_integer():
            details[key] = int(value)
    return details or None


def _extract_cost_details(payload: dict[str, Any]) -> dict[str, float] | None:
    usage = payload.get("usage")
    usage_dict = usage if isinstance(usage, dict) else {}
    details: dict[str, float] = {}
    for key in ("cost", "total_cost", "prompt_cost", "completion_cost"):
        value = usage_dict.get(key, payload.get(key))
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            details[key] = float(value)
    return details or None
