"""Unit tests for Zendesk comment resolution summarization."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from triage_service.adapters.jira_issue_fetcher import (
    FetchedIssue,
    LinkedZendeskTicket,
    ZendeskCommentRef,
)
from triage_service.adapters.openrouter_inference_client import (
    OpenRouterCompletionResult,
    OpenRouterInferenceError,
)
from triage_service.adapters.zendesk_comment_summarizer import (
    NoOpZendeskCommentSummarizer,
    OpenRouterZendeskCommentSummarizer,
    ZendeskCommentSummarizer,
    build_zendesk_comment_summarizer,
    parse_zendesk_resolution_summary,
    trim_zendesk_comments_for_budget,
)
from triage_service.core.settings import AppSettings


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> AppSettings:
    monkeypatch.setenv("JIRA_API_KEY", "jira-api-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-token")
    monkeypatch.setenv("TRIAGE_WEBHOOK_TOKEN", "triage-token")
    return AppSettings()


def _summary_response(
    *,
    initial_impact: str,
    latest_status: str,
    resolution_hints: str,
    open_risks: str,
) -> str:
    return (
        f"INITIAL_IMPACT:\n{initial_impact}\n\n"
        f"LATEST_STATUS:\n{latest_status}\n\n"
        f"RESOLUTION_HINTS:\n{resolution_hints}\n\n"
        f"OPEN_RISKS:\n{open_risks}"
    )


@pytest.mark.unit
def test_trim_zendesk_comments_newest_first_within_budget() -> None:
    comments = [
        ZendeskCommentRef(comment_id="3", body="newest", public=True),
        ZendeskCommentRef(comment_id="2", body="middle", public=True),
        ZendeskCommentRef(comment_id="1", body="oldest", public=False),
    ]
    trimmed = trim_zendesk_comments_for_budget(comments, char_budget=12)
    assert [comment.comment_id for comment in trimmed] == ["3", "2"]
    assert sum(len(comment.body) for comment in trimmed) <= 12


@pytest.mark.unit
def test_trim_zendesk_comments_returns_empty_when_budget_zero() -> None:
    comments = [ZendeskCommentRef(comment_id="1", body="note", public=True)]
    assert trim_zendesk_comments_for_budget(comments, char_budget=0) == []


@pytest.mark.unit
def test_parse_zendesk_resolution_summary_extracts_sections() -> None:
    parsed = parse_zendesk_resolution_summary(
        _summary_response(
            initial_impact="Customer reports checkout down.",
            latest_status="Service restored after vendor fix.",
            resolution_hints="Third-party payment outage; recovered.",
            open_risks="Monitor for recurrence.",
        ),
    )
    assert parsed is not None
    assert parsed.initial_impact == "Customer reports checkout down."
    assert parsed.latest_status == "Service restored after vendor fix."
    assert parsed.resolution_hints == "Third-party payment outage; recovered."
    assert parsed.open_risks == "Monitor for recurrence."


@pytest.mark.unit
def test_parse_zendesk_resolution_summary_returns_none_on_missing_section() -> None:
    assert parse_zendesk_resolution_summary("INITIAL_IMPACT:\nonly one section") is None


@pytest.mark.unit
def test_noop_summarizer_returns_tickets_unchanged() -> None:
    ticket = LinkedZendeskTicket(
        ticket_id="47322",
        subject="Outage",
        description="Major outage reported",
    )
    issue = FetchedIssue(
        issue_key="BC-1",
        summary="Checkout broken",
        issue_type="Bug",
        reporter="support",
    )
    summarizer = NoOpZendeskCommentSummarizer()
    result = summarizer.summarize(issue, [ticket], run_id="run-1")
    assert result.tickets == [ticket]
    assert result.tickets_considered == 0
    assert result.tickets_summarized == 0


@pytest.mark.unit
def test_build_zendesk_comment_summarizer_returns_noop_when_disabled(
    settings: AppSettings,
) -> None:
    summarizer = build_zendesk_comment_summarizer(settings)
    assert isinstance(summarizer, NoOpZendeskCommentSummarizer)


@pytest.mark.unit
def test_build_zendesk_comment_summarizer_returns_openrouter_when_enabled(
    settings: AppSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRIAGE_ZENDESK_COMMENT_SUMMARY_ENABLED", "true")
    enabled = AppSettings()
    summarizer = build_zendesk_comment_summarizer(enabled)
    assert isinstance(summarizer, OpenRouterZendeskCommentSummarizer)


@pytest.mark.unit
def test_zendesk_comment_summarizer_protocol_accepts_noop_implementation() -> None:
    summarizer: ZendeskCommentSummarizer = NoOpZendeskCommentSummarizer()
    assert hasattr(summarizer, "summarize")
    assert callable(summarizer.summarize)


@pytest.mark.unit
def test_openrouter_summarizer_calls_inference_and_attaches_summary(
    settings: AppSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRIAGE_ZENDESK_COMMENT_SUMMARY_ENABLED", "true")
    enabled = AppSettings()
    inference = MagicMock()
    inference.effective_model_id = "openai/gpt-4o-mini"
    inference.chat_completion_with_details.return_value = OpenRouterCompletionResult(
        content=_summary_response(
            initial_impact="Peak outage language in opening report.",
            latest_status="Vendor outage resolved; checkout working again.",
            resolution_hints="Temporary third-party payment provider outage.",
            open_risks="(none)",
        ),
    )
    ticket = LinkedZendeskTicket(
        ticket_id="47322",
        subject="Checkout outage",
        description="Everything is down",
        comments=[
            ZendeskCommentRef(
                comment_id="2",
                body="Third-party outage resolved; monitoring.",
                public=False,
                created_at="2026-05-29T12:00:00Z",
            ),
            ZendeskCommentRef(
                comment_id="1",
                body="Major outage affecting all customers.",
                public=True,
                created_at="2026-05-29T10:00:00Z",
            ),
        ],
    )
    issue = FetchedIssue(
        issue_key="BC-1",
        summary="Checkout broken",
        issue_type="Bug",
        reporter="support",
    )
    summarizer = OpenRouterZendeskCommentSummarizer(
        settings=enabled,
        inference_client=inference,
        comments_char_budget=enabled.triage_zendesk_comments_char_budget,
    )
    result = summarizer.summarize(issue, [ticket], run_id="run-1")
    inference.chat_completion_with_details.assert_called_once()
    summarized = result.tickets[0]
    assert summarized.resolution_summary is not None
    assert "resolved" in summarized.resolution_summary.latest_status.lower()
    assert "third-party" in summarized.resolution_summary.resolution_hints.lower()
    assert result.tickets_considered == 1
    assert result.tickets_summarized == 1


@pytest.mark.unit
def test_openrouter_summarizer_soft_fails_leaves_summary_none_on_inference_error(
    settings: AppSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRIAGE_ZENDESK_COMMENT_SUMMARY_ENABLED", "true")
    enabled = AppSettings()
    inference = MagicMock()
    inference.effective_model_id = "openai/gpt-4o-mini"
    inference.chat_completion_with_details.side_effect = OpenRouterInferenceError("timeout")
    ticket = LinkedZendeskTicket(ticket_id="47322", subject="Outage")
    issue = FetchedIssue(
        issue_key="BC-1",
        summary="s",
        issue_type="Bug",
        reporter="support",
    )
    summarizer = OpenRouterZendeskCommentSummarizer(
        settings=enabled,
        inference_client=inference,
        comments_char_budget=enabled.triage_zendesk_comments_char_budget,
    )
    result = summarizer.summarize(issue, [ticket], run_id="run-1")
    assert result.tickets[0].resolution_summary is None
    assert result.tickets_summarized == 0


@pytest.mark.unit
def test_openrouter_summarizer_soft_fails_leaves_summary_none_on_parse_failure(
    settings: AppSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRIAGE_ZENDESK_COMMENT_SUMMARY_ENABLED", "true")
    enabled = AppSettings()
    inference = MagicMock()
    inference.effective_model_id = "openai/gpt-4o-mini"
    inference.chat_completion_with_details.return_value = OpenRouterCompletionResult(
        content="unstructured prose only",
    )
    ticket = LinkedZendeskTicket(ticket_id="47322", subject="Outage")
    issue = FetchedIssue(
        issue_key="BC-1",
        summary="s",
        issue_type="Bug",
        reporter="support",
    )
    summarizer = OpenRouterZendeskCommentSummarizer(
        settings=enabled,
        inference_client=inference,
        comments_char_budget=enabled.triage_zendesk_comments_char_budget,
    )
    result = summarizer.summarize(issue, [ticket], run_id="run-1")
    assert result.tickets[0].resolution_summary is None
    assert result.tickets_summarized == 0


@pytest.mark.unit
def test_openrouter_summarizer_motivating_recovery_scenario(
    settings: AppSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Older severe impact plus newer recovery comment → summary emphasizes recovery."""
    monkeypatch.setenv("TRIAGE_ZENDESK_COMMENT_SUMMARY_ENABLED", "true")
    enabled = AppSettings()
    inference = MagicMock()
    inference.effective_model_id = "openai/gpt-4o-mini"
    inference.chat_completion_with_details.return_value = OpenRouterCompletionResult(
        content=_summary_response(
            initial_impact="Customer reported total checkout failure.",
            latest_status="Issue resolved; payments working after vendor recovery.",
            resolution_hints="Root cause was temporary third-party outage, now recovered.",
            open_risks="(none)",
        ),
    )
    ticket = LinkedZendeskTicket(
        ticket_id="47322",
        subject="P0 checkout down",
        description="All customers unable to pay",
        comments=[
            ZendeskCommentRef(
                comment_id="99",
                body="Confirmed third-party payment outage resolved; no action needed.",
                public=False,
                created_at="2026-05-29T18:00:00Z",
            ),
            ZendeskCommentRef(
                comment_id="1",
                body="CATASTROPHIC: entire platform offline for all users.",
                public=True,
                created_at="2026-05-29T09:00:00Z",
            ),
        ],
    )
    issue = FetchedIssue(
        issue_key="BC-1",
        summary="Checkout broken",
        issue_type="Bug",
        reporter="support",
    )
    summarizer = OpenRouterZendeskCommentSummarizer(
        settings=enabled,
        inference_client=inference,
        comments_char_budget=enabled.triage_zendesk_comments_char_budget,
    )
    result = summarizer.summarize(issue, [ticket], run_id="run-1")
    summary = result.tickets[0].resolution_summary
    assert summary is not None
    assert "resolved" in summary.latest_status.lower()
    assert "third-party" in summary.resolution_hints.lower()
    assert "catastrophic" not in summary.latest_status.lower()
