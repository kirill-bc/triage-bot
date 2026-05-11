"""Unit tests for OpenRouter chat completion client."""

from __future__ import annotations

import json

import httpx
import pytest

from openrouter_inference_client import OpenRouterInferenceClient, OpenRouterInferenceError
from settings import AppSettings


@pytest.fixture
def openrouter_app_settings(monkeypatch: pytest.MonkeyPatch) -> AppSettings:
    monkeypatch.setenv("JIRA_API_KEY", "jira-api-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-secret")
    monkeypatch.setenv("OPENROUTER_MODEL", "anthropic/claude-3-haiku")
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
def test_chat_completion_raises_on_http_error(openrouter_app_settings: AppSettings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="Rate limited")

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        inference = OpenRouterInferenceClient(openrouter_app_settings, client=client)
        with pytest.raises(OpenRouterInferenceError) as exc:
            inference.chat_completion(messages=[{"role": "user", "content": "hi"}])
    assert "429" in str(exc.value)


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
            inference.chat_completion(messages=[{"role": "user", "content": "x"}])
    assert "content" in str(exc.value).lower() or "empty" in str(exc.value).lower()


@pytest.mark.unit
def test_chat_completion_uses_default_model_when_env_omitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JIRA_API_KEY", "jira")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
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
        assert inference.chat_completion(messages=[{"role": "user", "content": "x"}]) == "ok"

    assert isinstance(captured["json"], dict)
    assert captured["json"]["model"] == settings.openrouter_model


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
            max_tokens=8,
        )

    payload = captured["json"]
    assert isinstance(payload, dict)
    assert payload.get("max_tokens") == 8
