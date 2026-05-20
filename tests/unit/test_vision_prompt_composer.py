"""Unit tests for vision preprocessor prompt composition."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from triage_service.adapters.jira_issue_fetcher import FetchedIssue
from triage_service.core.settings import AppSettings
from triage_service.core.vision_prompt_composer import (
    compose_vision_system_prompt,
    compose_vision_user_instruction,
    format_vision_issue_context,
)


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> AppSettings:
    monkeypatch.setenv("JIRA_API_KEY", "jira-api-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-token")
    monkeypatch.setenv("TRIAGE_WEBHOOK_TOKEN", "triage-token")
    return AppSettings()


def _sample_issue() -> FetchedIssue:
    return FetchedIssue(
        issue_key="TJC-42",
        summary="Checkout fails with 500",
        description="User sees a red error toast after clicking Pay.",
        reproduction_steps="1. Open cart\n2. Click Pay",
        issue_type="Bug",
        priority="P2",
        reporter="alice@example.com",
    )


@pytest.mark.unit
def test_format_vision_issue_context_includes_core_fields(
    settings: AppSettings,
) -> None:
    block = format_vision_issue_context(_sample_issue())
    assert "TJC-42" in block
    assert "Checkout fails with 500" in block
    assert "red error toast" in block
    assert "Click Pay" in block
    assert "alice@example.com" in block


@pytest.mark.unit
def test_vision_prompts_load_from_json_fallback(
    settings: AppSettings,
) -> None:
    system = compose_vision_system_prompt(settings=settings)
    user = compose_vision_user_instruction(_sample_issue(), settings=settings)
    assert "ticket" in system.lower() or "issue" in system.lower()
    assert "root cause" in system.lower()
    assert "TRANSCRIPT:" in user
    assert "SUMMARY:" in user
    assert "TJC-42" in user


@pytest.mark.unit
def test_vision_system_prompt_uses_langfuse_when_configured(
    settings: AppSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    monkeypatch.setenv(
        "TRIAGE_LANGFUSE_VISION_SYSTEM_PROMPT_NAME",
        "triagebot/vision-system",
    )

    fake_system = MagicMock()
    fake_system.compile.return_value = "LANGFUSE VISION SYSTEM"
    fake_client = MagicMock()
    fake_client.get_prompt.return_value = fake_system
    monkeypatch.setattr(
        "triage_service.core.langfuse_prompt_config.get_client",
        lambda: fake_client,
    )
    settings = AppSettings()

    assert compose_vision_system_prompt(settings=settings) == "LANGFUSE VISION SYSTEM"
    fake_client.get_prompt.assert_called_once()
    call_kwargs = fake_client.get_prompt.call_args.kwargs
    assert call_kwargs["type"] == "text"
    assert fake_client.get_prompt.call_args.args[0] == "triagebot/vision-system"


@pytest.mark.unit
def test_vision_user_instruction_uses_langfuse_when_configured(
    settings: AppSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    monkeypatch.setenv(
        "TRIAGE_LANGFUSE_VISION_USER_PROMPT_NAME",
        "triagebot/vision-user",
    )

    fake_user = MagicMock()
    fake_user.compile.return_value = "LANGFUSE VISION USER FORMAT"
    fake_client = MagicMock()
    fake_client.get_prompt.return_value = fake_user
    monkeypatch.setattr(
        "triage_service.core.langfuse_prompt_config.get_client",
        lambda: fake_client,
    )
    settings = AppSettings()

    user = compose_vision_user_instruction(_sample_issue(), settings=settings)
    assert user == "LANGFUSE VISION USER FORMAT"
    assert fake_client.get_prompt.call_args.args[0] == "triagebot/vision-user"
    compile_kwargs = fake_user.compile.call_args.kwargs
    assert "TJC-42" in compile_kwargs["issue_block"]


@pytest.mark.unit
def test_vision_prompts_fall_back_when_langfuse_disabled(
    settings: AppSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRIAGE_LANGFUSE_PROMPTS_ENABLED", "false")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")

    fake_client = MagicMock()
    monkeypatch.setattr(
        "triage_service.core.langfuse_prompt_config.get_client",
        lambda: fake_client,
    )
    settings = AppSettings()

    assert "TRANSCRIPT:" in compose_vision_user_instruction(_sample_issue(), settings=settings)
    fake_client.get_prompt.assert_not_called()


@pytest.mark.unit
def test_vision_prompts_fall_back_when_langfuse_fetch_fails(
    settings: AppSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")

    fake_client = MagicMock()
    fake_client.get_prompt.side_effect = RuntimeError("langfuse down")
    monkeypatch.setattr(
        "triage_service.core.langfuse_prompt_config.get_client",
        lambda: fake_client,
    )
    settings = AppSettings()

    system = compose_vision_system_prompt(settings=settings)
    assert "screenshots" in system.lower()
    assert fake_client.get_prompt.call_count >= 1
