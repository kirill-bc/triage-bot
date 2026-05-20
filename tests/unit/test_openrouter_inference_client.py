"""Unit tests for OpenRouter chat completion client."""

from __future__ import annotations

import json

import httpx
import pytest

from triage_service.adapters.openrouter_inference_client import (
    OpenRouterCompletionResult,
    OpenRouterInferenceClient,
    OpenRouterInferenceError,
)
from triage_service.core.settings import AppSettings


@pytest.fixture
def openrouter_app_settings(monkeypatch: pytest.MonkeyPatch) -> AppSettings:
    monkeypatch.setenv("JIRA_API_KEY", "jira-api-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-secret")
    monkeypatch.setenv("TRIAGE_WEBHOOK_TOKEN", "triage-token")
    monkeypatch.setenv("TRIAGE_TEXT_MODEL", "anthropic/claude-3-haiku")
    return AppSettings()


@pytest.mark.unit
def test_chat_completion_sends_model_from_settings_and_returns_assistant_text(
    openrouter_app_settings: AppSettings,
) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["method"] = request.method
        captured["auth"] = request.headers.get("Authorization", "")
        captured["json"] = json.loads(request.content.decode("utf-8"))
        assistant_json = '{"recommended_issue_type": "Bug"}'
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"role": "assistant", "content": assistant_json}},
                ],
            },
        )

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        inference = OpenRouterInferenceClient(openrouter_app_settings, client=client)
        text = inference.chat_completion(
            messages=[
                {"role": "system", "content": "You are a classifier."},
                {"role": "user", "content": "Classify this issue."},
            ],
            run_id="run-test",
        )

    assert text == '{"recommended_issue_type": "Bug"}'
    assert captured["method"] == "POST"
    assert str(captured["url"]).endswith("/api/v1/chat/completions")
    assert captured["auth"] == "Bearer openrouter-secret"
    payload = captured["json"]
    assert isinstance(payload, dict)
    assert payload["model"] == "anthropic/claude-3-haiku"
    assert payload["messages"] == [
        {"role": "system", "content": "You are a classifier."},
        {"role": "user", "content": "Classify this issue."},
    ]


@pytest.mark.unit
def test_chat_completion_uses_model_override_when_provided(
    openrouter_app_settings: AppSettings,
) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]},
        )

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        inference = OpenRouterInferenceClient(
            openrouter_app_settings,
            client=client,
            model_override="meta-llama/llama-3.1-8b-instruct",
        )
        assert inference.chat_completion(
            messages=[{"role": "user", "content": "x"}],
            run_id="run-test",
        ) == "ok"

    payload = captured["json"]
    assert isinstance(payload, dict)
    assert payload["model"] == "meta-llama/llama-3.1-8b-instruct"


@pytest.mark.unit
def test_chat_completion_raises_on_http_error(
    openrouter_app_settings: AppSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "triage_service.adapters.jira_http_retry.time.sleep",
        lambda *_a, **_k: None,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="Rate limited")

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        inference = OpenRouterInferenceClient(openrouter_app_settings, client=client)
        with pytest.raises(OpenRouterInferenceError) as exc:
            inference.chat_completion(
                messages=[{"role": "user", "content": "hi"}],
                run_id="run-test",
            )
    assert "429" in str(exc.value)
    err = exc.value
    assert isinstance(err, OpenRouterInferenceError)
    assert err.http_status == 429
    assert err.attempts == 3
    assert err.failure_category == "http_rate_limited"


@pytest.mark.unit
def test_chat_completion_raises_when_response_has_no_assistant_content(
    openrouter_app_settings: AppSettings,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        empty_choice = {"message": {"role": "assistant", "content": ""}}
        return httpx.Response(200, json={"choices": [empty_choice]})

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        inference = OpenRouterInferenceClient(openrouter_app_settings, client=client)
        with pytest.raises(OpenRouterInferenceError) as exc:
            inference.chat_completion(
                messages=[{"role": "user", "content": "x"}],
                run_id="run-test",
            )
    assert "content" in str(exc.value).lower() or "empty" in str(exc.value).lower()
    assert exc.value.failure_category == "invalid_upstream_payload"


@pytest.mark.unit
def test_chat_completion_uses_default_model_when_env_omitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JIRA_API_KEY", "jira")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    monkeypatch.setenv("TRIAGE_WEBHOOK_TOKEN", "triage-token")
    monkeypatch.delenv("TRIAGE_TEXT_MODEL", raising=False)
    settings = AppSettings()

    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]},
        )

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        inference = OpenRouterInferenceClient(settings, client=client)
        assert inference.chat_completion(
            messages=[{"role": "user", "content": "x"}],
            run_id="run-test",
        ) == "ok"

    assert isinstance(captured["json"], dict)
    assert captured["json"]["model"] == settings.triage_text_model


@pytest.mark.unit
def test_effective_model_id_prefers_override_then_settings(
    openrouter_app_settings: AppSettings,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]},
        )

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        default_inf = OpenRouterInferenceClient(openrouter_app_settings, client=client)
        assert default_inf.effective_model_id == "anthropic/claude-3-haiku"
        override_inf = OpenRouterInferenceClient(
            openrouter_app_settings,
            client=client,
            model_override="meta-llama/llama-3.1-8b-instruct",
        )
        assert override_inf.effective_model_id == "meta-llama/llama-3.1-8b-instruct"


@pytest.mark.unit
def test_chat_completion_includes_max_tokens_in_json_when_provided(
    openrouter_app_settings: AppSettings,
) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "x"}}]},
        )

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        inference = OpenRouterInferenceClient(openrouter_app_settings, client=client)
        inference.chat_completion(
            messages=[{"role": "user", "content": "hi"}],
            run_id="run-test",
            max_tokens=8,
        )

    payload = captured["json"]
    assert isinstance(payload, dict)
    assert payload.get("max_tokens") == 8


@pytest.mark.unit
def test_chat_completion_with_details_returns_usage_and_cost_when_present(
    openrouter_app_settings: AppSettings,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        _ = request
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                "usage": {
                    "prompt_tokens": 11,
                    "completion_tokens": 7,
                    "total_tokens": 18,
                    "cost": 0.00042,
                },
            },
        )

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        inference = OpenRouterInferenceClient(openrouter_app_settings, client=client)
        result = inference.chat_completion_with_details(
            messages=[{"role": "user", "content": "x"}],
            run_id="run-test",
        )

    assert isinstance(result, OpenRouterCompletionResult)
    assert result.content == "ok"
    assert result.usage_details == {
        "prompt_tokens": 11,
        "completion_tokens": 7,
        "total_tokens": 18,
    }
    assert result.cost_details == {"total": 0.00042}


@pytest.mark.unit
def test_chat_completion_with_details_maps_openrouter_prompt_and_completion_costs(
    openrouter_app_settings: AppSettings,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        _ = request
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                "usage": {
                    "prompt_tokens": 11,
                    "completion_tokens": 7,
                    "total_tokens": 18,
                    "prompt_cost": 0.0003,
                    "completion_cost": 0.00012,
                    "total_cost": 0.00042,
                },
            },
        )

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        inference = OpenRouterInferenceClient(openrouter_app_settings, client=client)
        result = inference.chat_completion_with_details(
            messages=[{"role": "user", "content": "x"}],
            run_id="run-test",
        )

    assert isinstance(result, OpenRouterCompletionResult)
    assert result.cost_details == {
        "input": 0.0003,
        "output": 0.00012,
        "total": 0.00042,
    }


@pytest.mark.unit
def test_chat_completion_retries_on_502_then_succeeds(
    openrouter_app_settings: AppSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "triage_service.adapters.jira_http_retry.time.sleep",
        lambda *_a, **_k: None,
    )
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        _ = request
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(502, text="bad gateway")
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "recovered"}}]},
        )

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        inference = OpenRouterInferenceClient(openrouter_app_settings, client=client)
        text = inference.chat_completion(
            messages=[{"role": "user", "content": "x"}],
            run_id="run-retry",
        )
    assert text == "recovered"
    assert calls["n"] == 2


@pytest.mark.unit
def test_chat_completion_raises_after_retries_exhausted_on_503(
    openrouter_app_settings: AppSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "triage_service.adapters.jira_http_retry.time.sleep",
        lambda *_a, **_k: None,
    )
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        _ = request
        calls["n"] += 1
        return httpx.Response(503, text="unavailable")

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        inference = OpenRouterInferenceClient(openrouter_app_settings, client=client)
        with pytest.raises(OpenRouterInferenceError) as exc:
            inference.chat_completion(
                messages=[{"role": "user", "content": "x"}],
                run_id="run-exhaust",
            )
    assert "503" in str(exc.value)
    assert calls["n"] == 3
    assert exc.value.attempts == 3
    assert exc.value.http_status == 503
    assert exc.value.failure_category == "http_transient"


@pytest.mark.unit
def test_chat_completion_retries_on_connect_error_then_succeeds(
    openrouter_app_settings: AppSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "triage_service.adapters.jira_http_retry.time.sleep",
        lambda *_a, **_k: None,
    )
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]},
        )

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        inference = OpenRouterInferenceClient(openrouter_app_settings, client=client)
        assert inference.chat_completion(
            messages=[{"role": "user", "content": "x"}],
            run_id="run-transport",
        ) == "ok"
    assert calls["n"] == 2


@pytest.mark.unit
def test_chat_completion_wraps_request_error_after_retries(
    openrouter_app_settings: AppSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "triage_service.adapters.jira_http_retry.time.sleep",
        lambda *_a, **_k: None,
    )
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ConnectError("always down", request=request)

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        inference = OpenRouterInferenceClient(openrouter_app_settings, client=client)
        with pytest.raises(OpenRouterInferenceError) as exc:
            inference.chat_completion(
                messages=[{"role": "user", "content": "x"}],
                run_id="run-dead",
            )
    assert calls["n"] == 3
    assert "retries" in str(exc.value).lower()
    assert exc.value.attempts == 3
    assert exc.value.transport_error_kind == "connect_error"
    assert exc.value.failure_category == "connect_error"


@pytest.mark.unit
def test_chat_completion_raises_on_500_with_http_error_failure_category(
    openrouter_app_settings: AppSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "triage_service.adapters.jira_http_retry.time.sleep",
        lambda *_a, **_k: None,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal")

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        inference = OpenRouterInferenceClient(openrouter_app_settings, client=client)
        with pytest.raises(OpenRouterInferenceError) as exc:
            inference.chat_completion(
                messages=[{"role": "user", "content": "x"}],
                run_id="run-500",
            )
    assert exc.value.http_status == 500
    assert exc.value.attempts == 1
    assert exc.value.failure_category == "http_error"


@pytest.mark.unit
def test_chat_completion_read_timeout_after_retries_sets_failure_category_timeout(
    openrouter_app_settings: AppSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "triage_service.adapters.jira_http_retry.time.sleep",
        lambda *_a, **_k: None,
    )
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ReadTimeout("slow", request=request)

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        inference = OpenRouterInferenceClient(openrouter_app_settings, client=client)
        with pytest.raises(OpenRouterInferenceError) as exc:
            inference.chat_completion(
                messages=[{"role": "user", "content": "x"}],
                run_id="run-timeout",
            )
    assert calls["n"] == 3
    assert exc.value.attempts == 3
    assert exc.value.transport_timeout is True
    assert exc.value.transport_error_kind == "timeout"
    assert exc.value.failure_category == "timeout"


@pytest.mark.unit
def test_chat_completion_zero_retries_fails_immediately_on_429(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JIRA_API_KEY", "jira-api-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-secret")
    monkeypatch.setenv("TRIAGE_WEBHOOK_TOKEN", "triage-token")
    monkeypatch.setenv("TRIAGE_TEXT_MODEL", "openai/gpt-4o-mini")
    monkeypatch.setenv("TRIAGE_OPENROUTER_HTTP_MAX_RETRIES", "0")
    settings = AppSettings()
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429, text="rate limited")

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        inference = OpenRouterInferenceClient(settings, client=client)
        with pytest.raises(OpenRouterInferenceError) as exc:
            inference.chat_completion(
                messages=[{"role": "user", "content": "x"}],
                run_id="run-429",
            )
    assert calls["n"] == 1
    assert exc.value.attempts == 1
    assert exc.value.http_status == 429
    assert exc.value.failure_category == "http_rate_limited"
