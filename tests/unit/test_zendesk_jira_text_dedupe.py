"""Unit tests for post-render Jira vs Zendesk resolution-summary text dedupe."""

from __future__ import annotations

import pytest

from triage_service.adapters.jira_issue_fetcher import (
    FetchedIssue,
    LinkedZendeskTicket,
    ZendeskResolutionSummary,
)
from triage_service.core.issue_text_block import format_issue_text_block
from triage_service.core.zendesk_jira_text_dedupe import (
    JIRA_ECHO_PLACEHOLDER,
    resolution_summary_section_echoes_jira,
    sanitize_resolution_summary_against_jira,
)


@pytest.mark.unit
def test_section_echoes_jira_when_verbatim_substring_of_description() -> None:
    issue = FetchedIssue(
        issue_key="BC-1",
        summary="Checkout broken",
        issue_type="Bug",
        reporter="support",
        description="Payment gateway returned 503 for all checkout attempts.",
    )
    assert resolution_summary_section_echoes_jira(
        "Payment gateway returned 503 for all checkout attempts.",
        issue,
    )


@pytest.mark.unit
def test_section_echoes_jira_is_case_and_whitespace_insensitive() -> None:
    jira_summary = "Users cannot sign in after password reset on mobile."
    issue = FetchedIssue(
        issue_key="BC-2",
        summary=jira_summary,
        issue_type="Bug",
        reporter="support",
    )
    assert resolution_summary_section_echoes_jira(
        "users cannot sign in after password reset on mobile.",
        issue,
    )


@pytest.mark.unit
def test_section_echoes_jira_false_for_short_generic_phrases() -> None:
    issue = FetchedIssue(
        issue_key="BC-3",
        summary="Minor UI glitch",
        issue_type="Bug",
        reporter="support",
        description="Button misaligned on settings page.",
    )
    assert not resolution_summary_section_echoes_jira("(none)", issue)


@pytest.mark.unit
def test_section_echoes_jira_false_for_net_new_signal() -> None:
    issue = FetchedIssue(
        issue_key="BC-4",
        summary="Checkout broken",
        issue_type="Bug",
        reporter="support",
        description="Users cannot complete purchase.",
    )
    assert not resolution_summary_section_echoes_jira(
        "Vendor outage resolved overnight; monitoring continues.",
        issue,
    )


@pytest.mark.unit
def test_sanitize_resolution_summary_replaces_verbatim_jira_sections() -> None:
    jira_description = "Entire platform unavailable since 09:00 UTC."
    issue = FetchedIssue(
        issue_key="BC-5",
        summary="Platform down",
        issue_type="Bug",
        reporter="support",
        description=jira_description,
    )
    summary = ZendeskResolutionSummary(
        initial_impact=jira_description,
        latest_status="Recovered after vendor DNS fix.",
        resolution_hints="Temporary third-party DNS outage.",
        open_risks="(none)",
    )

    sanitized = sanitize_resolution_summary_against_jira(summary, issue)

    assert sanitized.initial_impact == JIRA_ECHO_PLACEHOLDER
    assert sanitized.latest_status == summary.latest_status
    assert sanitized.resolution_hints == summary.resolution_hints


@pytest.mark.unit
def test_format_issue_text_block_replaces_verbatim_jira_echo_in_resolution_signals() -> None:
    jira_description = "Payment gateway returned 503 for all checkout attempts."
    issue = FetchedIssue(
        issue_key="BC-6",
        summary="Checkout broken",
        issue_type="Bug",
        reporter="support",
        description=jira_description,
        zendesk_tickets=[
            LinkedZendeskTicket(
                ticket_id="47322",
                subject="Checkout outage",
                description="Zendesk raw description",
                status="solved",
                priority="urgent",
                resolution_summary=ZendeskResolutionSummary(
                    initial_impact=jira_description,
                    latest_status="Vendor restored service; checkout working.",
                    resolution_hints="Third-party outage; no customer action needed.",
                    open_risks="(none)",
                ),
            ),
        ],
    )

    text = format_issue_text_block(issue)

    assert JIRA_ECHO_PLACEHOLDER in text
    assert jira_description not in text.split("Linked Zendesk tickets:")[1]
    assert "Vendor restored service" in text
