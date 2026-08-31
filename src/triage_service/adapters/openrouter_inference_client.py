"""OpenRouter chat completions client (OpenAI-compatible API)."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
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
        http_timeout_seconds: float | None = None,
        client_factory: Callable[[], httpx.Client] | None = None,
    ) -> None:
        """Build a client. ``client`` and ``client_factory`` are test-injection hooks.

        If both are set, ``client`` wins: ``chat_completion_with_details`` posts on
        that instance and skips the deadline wrapper, so ``client_factory`` is unused.
        """
        self._settings = settings
        self._client = client
        stripped = model_override.strip() if model_override else ""
        self._model_override = stripped or None
        self._http_timeout_seconds = http_timeout_seconds
        self._client_factory = client_factory

    @property
    def effective_model_id(self) -> str:
        """Resolved OpenRouter model id (override or configured default)."""
        return self._model_override or self._settings.triage_text_model

    @property
    def _effective_http_timeout_seconds(self) -> float:
        if self._http_timeout_seconds is not None:
            return self._http_timeout_seconds
        return self._settings.openrouter_http_timeout_seconds

    def _new_client(self) -> httpx.Client:
        if self._client_factory is not None:
            return self._client_factory()
        return httpx.Client(timeout=httpx.Timeout(self._effective_http_timeout_seconds))

    def chat_completion(
        self,
        messages: list[dict[str, Any]],
        *,
        run_id: str,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        json_object_response: bool = False,
    ) -> str:
        return self.chat_completion_with_details(
            messages,
            run_id=run_id,
            temperature=temperature,
            max_tokens=max_tokens,
            json_object_response=json_object_response,
        ).content

    def chat_completion_with_details(
        self,
        messages: list[dict[str, Any]],
        *,
        run_id: str,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        json_object_response: bool = False,
    ) -> OpenRouterCompletionResult:
        _ = run_id
        headers = {
            "Authorization": f"Bearer {self._settings.openrouter_api_key}",
            "Content-Type": "application/json",
        }
        model_id = self._model_override or self._settings.triage_text_model
        body: dict[str, Any] = {
            "model": model_id,
            "messages": list(messages),
            "temperature": temperature,
        }
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        if json_object_response:
            # Ask providers for a bare JSON object; require_parameters keeps OpenRouter
            # from routing to providers (e.g. under a `:nitro` alias) that ignore
            # response_format and would otherwise wrap replies in prose or code fences.
            body["response_format"] = {"type": "json_object"}
            body["provider"] = {"require_parameters": True}
        if self._client is not None:
            return self._post(self._client, body, headers)
        return self._post_within_deadline(body, headers)

    def _post_within_deadline(
        self,
        body: dict[str, Any],
        headers: dict[str, str],
    ) -> OpenRouterCompletionResult:
        """POST with a hard wall-clock ceiling and one fresh-connection retry.

        The httpx read timeout only bounds the gap between received chunks, so it cannot
        bound a call that OpenRouter keeps alive with padding while an upstream provider
        stalls. A watchdog closes the connection once the deadline passes, which unblocks
        the in-flight read.
        """
        deadline = self._settings.openrouter_call_deadline_seconds
        max_outer_attempts = 2
        for attempt in range(max_outer_attempts):
            client = self._new_client()
            pool = ThreadPoolExecutor(max_workers=1)
            future = pool.submit(self._post, client, body, headers)
            try:
                result = future.result(timeout=deadline)
            except FutureTimeoutError:
                self._release(client, pool)
                if attempt + 1 >= max_outer_attempts:
                    raise OpenRouterInferenceError(
                        f"OpenRouter call exceeded the {deadline}s wall-clock deadline on "
                        f"{max_outer_attempts} attempts.",
                        attempts=max_outer_attempts,
                        transport_timeout=True,
                        transport_error_kind="deadline_exceeded",
                        failure_category="timeout",
                    ) from None
                continue
            except OpenRouterInferenceError as exc:
                self._release(client, pool)
                retriable = exc.failure_category == "invalid_upstream_payload"
                if retriable and attempt + 1 < max_outer_attempts:
                    continue
                raise
            except Exception:
                self._release(client, pool)
                raise
            self._release(client, pool)
            return result
        raise RuntimeError("_post_within_deadline: unreachable")

    @staticmethod
    def _release(client: httpx.Client, pool: ThreadPoolExecutor) -> None:
        """Close the HTTP client and shut down the watchdog pool (success or error)."""
        try:
            client.close()
        except Exception:  # pragma: no cover - close is best-effort
            pass
        pool.shutdown(wait=False)

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
        payload, content = _parse_completion_payload(response, attempts)
        usage_details = _extract_usage_details(payload)
        cost_details = _extract_cost_details(payload)
        return OpenRouterCompletionResult(
            content=content,
            usage_details=usage_details,
            cost_details=cost_details,
        )


def _parse_completion_payload(
    response: httpx.Response,
    attempts: int,
) -> tuple[dict[str, Any], str]:
    """Return ``(payload, assistant_content)`` or raise for an unusable HTTP 200 body."""
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
    return payload, content


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
