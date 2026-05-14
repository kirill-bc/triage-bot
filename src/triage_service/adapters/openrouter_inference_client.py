"""OpenRouter chat completions client (OpenAI-compatible API)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from triage_service.adapters.jira_http_retry import (
    TransportRetriesExhausted,
    classify_transport_request_error,
    request_with_retries,
)
from triage_service.core.settings import AppSettings

OPENROUTER_CHAT_COMPLETIONS_URL = "https://openrouter.ai/api/v1/chat/completions"


class OpenRouterInferenceError(RuntimeError):
    """Raised when OpenRouter returns an error or an unusable completion payload."""

    def __init__(
        self,
        message: str,
        *,
        attempts: int | None = None,
        http_status: int | None = None,
        transport_timeout: bool | None = None,
        transport_error_kind: str | None = None,
        failure_category: str | None = None,
    ) -> None:
        super().__init__(message)
        self.attempts = attempts
        self.http_status = http_status
        self.transport_timeout = transport_timeout
        self.transport_error_kind = transport_error_kind
        self.failure_category = failure_category


def _failure_category_for_http_status(status: int) -> str:
    if status == 429:
        return "http_rate_limited"
    if status in (502, 503, 504):
        return "http_transient"
    return "http_error"


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

        timeout = httpx.Timeout(self._settings.openrouter_http_timeout_seconds)
        with httpx.Client(timeout=timeout) as client:
            return self._post(client, body, headers)

    def _post(
        self,
        client: httpx.Client,
        body: dict[str, Any],
        headers: dict[str, str],
    ) -> OpenRouterCompletionResult:
        try:
            response, attempts = request_with_retries(
                client,
                "POST",
                OPENROUTER_CHAT_COMPLETIONS_URL,
                max_retries=self._settings.openrouter_http_max_retries,
                headers=headers,
                json=body,
            )
        except TransportRetriesExhausted as tre:
            timeout, kind = classify_transport_request_error(tre.cause)
            category = "timeout" if timeout else kind
            raise OpenRouterInferenceError(
                f"OpenRouter request failed after retries: {tre.cause}",
                attempts=tre.attempts,
                transport_timeout=timeout,
                transport_error_kind=kind,
                failure_category=category,
            ) from tre.cause
        except httpx.RequestError as exc:
            timeout, kind = classify_transport_request_error(exc)
            category = "timeout" if timeout else kind
            raise OpenRouterInferenceError(
                f"OpenRouter request failed: {exc}",
                attempts=1,
                transport_timeout=timeout,
                transport_error_kind=kind,
                failure_category=category,
            ) from exc
        if response.is_error:
            snippet = response.text[:200]
            fc = _failure_category_for_http_status(response.status_code)
            raise OpenRouterInferenceError(
                f"OpenRouter request failed with HTTP {response.status_code}: {snippet}",
                attempts=attempts,
                http_status=response.status_code,
                failure_category=fc,
            )
        payload = response.json()
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise OpenRouterInferenceError(
                "OpenRouter response missing choices.",
                attempts=attempts,
                failure_category="invalid_upstream_payload",
            )
        first = choices[0]
        if not isinstance(first, dict):
            raise OpenRouterInferenceError(
                "OpenRouter response has invalid choice shape.",
                attempts=attempts,
                failure_category="invalid_upstream_payload",
            )
        message = first.get("message")
        if not isinstance(message, dict):
            raise OpenRouterInferenceError(
                "OpenRouter response missing message object.",
                attempts=attempts,
                failure_category="invalid_upstream_payload",
            )
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise OpenRouterInferenceError(
                "OpenRouter response missing non-empty assistant content.",
                attempts=attempts,
                failure_category="invalid_upstream_payload",
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
    total_cost = usage_dict.get("total_cost", payload.get("total_cost"))
    if total_cost is None:
        total_cost = usage_dict.get("cost", payload.get("cost"))
    prompt_cost = usage_dict.get("prompt_cost", payload.get("prompt_cost"))
    completion_cost = usage_dict.get("completion_cost", payload.get("completion_cost"))

    if isinstance(total_cost, (int, float)) and not isinstance(total_cost, bool):
        details["total"] = float(total_cost)
    if isinstance(prompt_cost, (int, float)) and not isinstance(prompt_cost, bool):
        details["input"] = float(prompt_cost)
    if isinstance(completion_cost, (int, float)) and not isinstance(completion_cost, bool):
        details["output"] = float(completion_cost)
    return details or None
