"""Unit tests for Zendesk summary prompt composition."""

from __future__ import annotations

import pytest

from triage_service.adapters.jira_issue_fetcher import FetchedIssue, LinkedZendeskTicket
from triage_service.core.settings import AppSettings
from triage_service.core.zendesk_summary_prompt_composer import (
    compose_zendesk_summary_user_instruction,
    format_jira_context_for_zendesk_summary,
)


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> AppSettings:
    monkeypatch.setenv("JIRA_API_KEY", "jira-api-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-token")
    monkeypatch.setenv("TRIAGE_WEBHOOK_TOKEN", "triage-token")
    return AppSettings()


@pytest.mark.unit
def test_format_jira_context_includes_summary_description_and_repro() -> None:
    issue = FetchedIssue(
        issue_key="BC-10",
        summary="Checkout broken",
        issue_type="Bug",
        reporter="support",
        description="503 on payment API.",
        reproduction_steps="Open checkout and pay with card.",
    )

    context = format_jira_context_for_zendesk_summary(issue)

    assert "Issue key: BC-10" in context
    assert "Checkout broken" in context
    assert "503 on payment API." in context
    assert "Open checkout and pay with card." in context


@pytest.mark.unit
def test_compose_zendesk_summary_user_instruction_instructs_net_new_signal(
    settings: AppSettings,
) -> None:
    issue = FetchedIssue(
        issue_key="BC-11",
        summary="Login loop",
        issue_type="Bug",
        reporter="support",
        description="MFA prompt repeats.",
    )
    ticket = LinkedZendeskTicket(
        ticket_id="99",
        subject="Cannot sign in",
        description="Zendesk-side description",
    )

    instruction = compose_zendesk_summary_user_instruction(
        issue,
        ticket,
        [],
        settings=settings,
    )

    assert "net-new signal" in instruction.lower()
    assert "Jira issue context" in instruction
    assert "Login loop" in instruction
    assert "MFA prompt repeats." in instruction
