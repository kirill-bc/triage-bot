"""Unit tests for optional Zendesk ticket enrichment."""

from __future__ import annotations

import httpx
import pytest

from triage_service.adapters.jira_issue_fetcher import (
    FetchedIssue,
    parse_zendesk_ticket_ids_from_field_value,
)
from triage_service.adapters.zendesk_ticket_fetcher import (
    ZendeskTicketFetcher,
    extract_zendesk_ticket_ids,
)
from triage_service.core.settings import AppSettings


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> AppSettings:
    monkeypatch.setenv("JIRA_API_KEY", "jira-api-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-token")
    monkeypatch.setenv("TRIAGE_WEBHOOK_TOKEN", "triage-token")
    monkeypatch.setenv("TRIAGE_ZENDESK_CONTEXT_ENABLED", "true")
    monkeypatch.setenv("ZENDESK_BASE_URL", "https://acme.zendesk.com")
    monkeypatch.setenv("ZENDESK_USER_EMAIL", "agent@example.com")
    monkeypatch.setenv("ZENDESK_API_TOKEN", "zd-token")
    return AppSettings()


@pytest.mark.unit
def test_parse_zendesk_ticket_ids_from_field_value_multiline_and_urls() -> None:
    raw = "5001\n5002, 5003\nhttps://acme.zendesk.com/agent/tickets/5004"
    assert parse_zendesk_ticket_ids_from_field_value(raw) == [
        "5001",
        "5002",
        "5003",
        "5004",
    ]


@pytest.mark.unit
def test_extract_zendesk_ticket_ids_supports_urls_and_short_tokens() -> None:
    ids = extract_zendesk_ticket_ids(
        (
            "https://acme.zendesk.com/agent/tickets/12345",
            "duplicate zd-12345 should dedupe",
            "customer linked ZD #998877 for same case",
        ),
    )
    assert ids == ["12345", "998877"]


@pytest.mark.unit
def test_collect_linked_ticket_ids_prefers_jira_custom_fields(settings: AppSettings) -> None:
    issue = FetchedIssue(
        issue_key="BC-9",
        summary="mentions ZD-999 in body only",
        issue_type="Bug",
        reporter="support",
        zendesk_ticket_ids=["5001", "5002"],
    )
    fetcher = ZendeskTicketFetcher(settings)
    assert fetcher.collect_linked_ticket_ids(issue) == ["5001", "5002"]


@pytest.mark.unit
def test_collect_linked_ticket_ids_returns_all_custom_field_ids_without_cap(
    settings: AppSettings,
) -> None:
    issue = FetchedIssue(
        issue_key="BC-9",
        summary="mentions ZD-999 in body only",
        issue_type="Bug",
        reporter="support",
        zendesk_ticket_ids=["47322", "48661", "48950", "48951"],
    )
    fetcher = ZendeskTicketFetcher(settings)
    assert fetcher.collect_linked_ticket_ids(issue) == [
        "47322",
        "48661",
        "48950",
        "48951",
    ]


@pytest.mark.unit
def test_collect_linked_ticket_ids_caps_body_text_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JIRA_API_KEY", "jira-api-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-token")
    monkeypatch.setenv("TRIAGE_WEBHOOK_TOKEN", "triage-token")
    monkeypatch.setenv("TRIAGE_ZENDESK_MAX_TICKETS", "2")
    limited = AppSettings()
    issue = FetchedIssue(
        issue_key="BC-9",
        summary="ZD-100 ZD-200 ZD-300",
        issue_type="Bug",
        reporter="support",
    )
    fetcher = ZendeskTicketFetcher(limited)
    assert fetcher.collect_linked_ticket_ids(issue) == ["100", "200"]


@pytest.mark.unit
def test_fetch_linked_tickets_enriches_issue_when_configured(settings: AppSettings) -> None:
    issue = FetchedIssue(
        issue_key="BC-1",
        summary="ignored when custom field ids present",
        issue_type="Bug",
        reporter="support",
        zendesk_ticket_ids=["777", "888"],
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("Authorization", "").startswith("Basic ")
        assert request.url.path in ("/api/v2/tickets/777.json", "/api/v2/tickets/888.json")
        ticket_id = request.url.path.split("/")[-1].replace(".json", "")
        return httpx.Response(
            200,
            json={
                "ticket": {
                    "id": int(ticket_id),
                    "subject": f"Subject {ticket_id}",
                    "description": f"Desc {ticket_id}",
                    "status": "open",
                    "priority": "high",
                },
            },
        )

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        fetcher = ZendeskTicketFetcher(settings, client=client)
        tickets = fetcher.fetch_linked_tickets(issue, run_id="run-zd")

    assert [t.ticket_id for t in tickets] == ["777", "888"]
    assert tickets[0].subject == "Subject 777"
    assert tickets[0].url == "https://acme.zendesk.com/agent/tickets/777"


@pytest.mark.unit
def test_fetch_linked_tickets_returns_empty_when_feature_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JIRA_API_KEY", "jira-api-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-token")
    monkeypatch.setenv("TRIAGE_WEBHOOK_TOKEN", "triage-token")
    monkeypatch.setenv("TRIAGE_ZENDESK_CONTEXT_ENABLED", "false")
    settings = AppSettings()
    issue = FetchedIssue(
        issue_key="BC-2",
        summary="ZD-101",
        issue_type="Bug",
        reporter="support",
        zendesk_ticket_ids=["101"],
    )
    fetcher = ZendeskTicketFetcher(settings)
    assert fetcher.fetch_linked_tickets(issue, run_id="run-zd-disabled") == []
