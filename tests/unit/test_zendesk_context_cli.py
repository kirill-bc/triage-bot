"""Unit tests for Zendesk context CLI summary output."""

from __future__ import annotations

import pytest

from triage_service.adapters.jira_issue_fetcher import (
    LinkedZendeskTicket,
    ZendeskResolutionSummary,
)
from triage_service.adapters.zendesk_context_cli import (
    ZendeskContextEnrichmentResult,
    build_cli_zendesk_context_summary,
)


@pytest.mark.unit
def test_build_cli_zendesk_context_summary_when_disabled() -> None:
    assert build_cli_zendesk_context_summary(enabled=False, enrichment=None) == {
        "enabled": False,
    }


@pytest.mark.unit
def test_build_cli_zendesk_context_summary_when_enabled_no_tickets() -> None:
    summary = build_cli_zendesk_context_summary(
        enabled=True,
        enrichment=ZendeskContextEnrichmentResult(
            ticket_ids_requested=[],
            tickets_fetched=0,
        ),
    )
    assert summary == {
        "enabled": True,
        "ticket_ids_requested": [],
        "tickets_fetched": 0,
        "tickets_summarized": 0,
        "tickets": [],
    }


@pytest.mark.unit
def test_build_cli_zendesk_context_summary_includes_resolution_signals() -> None:
    enrichment = ZendeskContextEnrichmentResult(
        ticket_ids_requested=["47322"],
        tickets_fetched=1,
        tickets_summarized=1,
        total_summary_cost=0.002,
        tickets=[
            LinkedZendeskTicket(
                ticket_id="47322",
                subject="Checkout outage",
                description="Everything is down",
                status="solved",
                priority="urgent",
                resolution_summary=ZendeskResolutionSummary(
                    initial_impact="All checkout attempts failing.",
                    latest_status="Vendor restored service.",
                    resolution_hints="Temporary third-party outage.",
                    open_risks="(none)",
                ),
            ),
        ],
    )

    summary = build_cli_zendesk_context_summary(enabled=True, enrichment=enrichment)

    assert summary["enabled"] is True
    assert summary["tickets_summarized"] == 1
    assert summary["total_summary_cost"] == 0.002
    tickets = summary["tickets"]
    assert isinstance(tickets, list)
    ticket = tickets[0]
    assert isinstance(ticket, dict)
    assert ticket["ticket_id"] == "47322"
    assert ticket["render_mode"] == "resolution_signals"
    assert ticket["resolution_signals"]["latest_status"] == "Vendor restored service."


@pytest.mark.unit
def test_build_cli_zendesk_context_summary_falls_back_to_subject_description() -> None:
    enrichment = ZendeskContextEnrichmentResult(
        ticket_ids_requested=["88"],
        tickets_fetched=1,
        tickets=[
            LinkedZendeskTicket(
                ticket_id="88",
                subject="Cannot sign in",
                description="MFA loop after password reset.",
                status="open",
                priority="normal",
            ),
        ],
    )

    summary = build_cli_zendesk_context_summary(enabled=True, enrichment=enrichment)
    tickets = summary["tickets"]
    assert isinstance(tickets, list)
    ticket = tickets[0]
    assert isinstance(ticket, dict)

    assert ticket["render_mode"] == "subject_description"
    assert ticket["subject"] == "Cannot sign in"
    assert ticket["description"] == "MFA loop after password reset."
