"""Unit tests for issue text block formatting."""

from __future__ import annotations

import pytest

from triage_service.adapters.image_context_extractor import ImageContext
from triage_service.adapters.jira_issue_fetcher import (
    CommentRef,
    FetchedIssue,
    LinkedZendeskTicket,
    ZendeskResolutionSummary,
)
from triage_service.core.issue_text_block import format_issue_text_block


@pytest.mark.unit
def test_format_issue_text_block_includes_comments_when_present() -> None:
    issue = FetchedIssue(
        issue_key="TJC-300",
        summary="Login fails",
        description="Cannot log in on Safari",
        issue_type="Bug",
        priority="P2",
        reporter="Alice",
        comments=[
            CommentRef(
                id="1",
                author="Bob",
                created="2026-05-29T11:00:00.000+0000",
                body="I can reproduce this issue",
                attachment_ids=[],
            ),
        ],
    )

    text = format_issue_text_block(issue, comments_char_budget=500)

    assert "Comments:" in text
    assert "Bob" in text
    assert "I can reproduce this issue" in text


@pytest.mark.unit
def test_format_issue_text_block_truncates_oldest_comments_when_over_budget() -> None:
    issue = FetchedIssue(
        issue_key="TJC-301",
        summary="Login fails",
        description="Cannot log in on Safari",
        issue_type="Bug",
        priority="P2",
        reporter="Alice",
        comments=[
            CommentRef(
                id="1",
                author="Old",
                created="2026-05-29T10:00:00.000+0000",
                body="old comment should be dropped first",
                attachment_ids=[],
            ),
            CommentRef(
                id="2",
                author="New",
                created="2026-05-29T11:00:00.000+0000",
                body="newest comment should remain",
                attachment_ids=[],
            ),
        ],
    )

    text = format_issue_text_block(issue, comments_char_budget=40)

    assert "Comments:" in text
    assert "newest comment should remain" in text
    assert "old comment should be dropped first" not in text


@pytest.mark.unit
def test_format_issue_text_block_shows_none_when_no_comments() -> None:
    issue = FetchedIssue(
        issue_key="TJC-303",
        summary="Login fails",
        description="Cannot log in on Safari",
        issue_type="Bug",
        priority="P2",
        reporter="Alice",
        comments=[],
    )

    text = format_issue_text_block(issue, comments_char_budget=500)

    assert "Comments:\n(none)" in text


@pytest.mark.unit
def test_format_issue_text_block_shows_omitted_when_budget_zero() -> None:
    issue = FetchedIssue(
        issue_key="TJC-304",
        summary="Login fails",
        description="Cannot log in on Safari",
        issue_type="Bug",
        priority="P2",
        reporter="Alice",
        comments=[
            CommentRef(
                id="1",
                author="Bob",
                created="2026-05-29T11:00:00.000+0000",
                body="I can reproduce this issue",
                attachment_ids=[],
            ),
        ],
    )

    text = format_issue_text_block(issue, comments_char_budget=0)

    assert "Comments:\n(omitted by comment budget)" in text
    assert "I can reproduce this issue" not in text


@pytest.mark.unit
def test_format_issue_text_block_shows_omitted_when_all_comments_exceed_budget() -> None:
    issue = FetchedIssue(
        issue_key="TJC-305",
        summary="Login fails",
        description="Cannot log in on Safari",
        issue_type="Bug",
        priority="P2",
        reporter="Alice",
        comments=[
            CommentRef(
                id="1",
                author="Bob",
                created="2026-05-29T11:00:00.000+0000",
                body="this comment is too long for the budget",
                attachment_ids=[],
            ),
        ],
    )

    text = format_issue_text_block(issue, comments_char_budget=5)

    assert "Comments:\n(omitted by comment budget)" in text
    assert "this comment is too long for the budget" not in text


@pytest.mark.unit
def test_format_issue_text_block_keeps_contiguous_newest_comment_suffix() -> None:
    issue = FetchedIssue(
        issue_key="TJC-302",
        summary="Login fails",
        description="Cannot log in on Safari",
        issue_type="Bug",
        priority="P2",
        reporter="Alice",
        comments=[
            CommentRef(
                id="1",
                author="Old",
                created="2026-05-29T10:00:00.000+0000",
                body="short old",
                attachment_ids=[],
            ),
            CommentRef(
                id="2",
                author="Mid",
                created="2026-05-29T10:30:00.000+0000",
                body="medium middle comment",
                attachment_ids=[],
            ),
            CommentRef(
                id="3",
                author="New",
                created="2026-05-29T11:00:00.000+0000",
                body="new",
                attachment_ids=[],
            ),
        ],
    )

    text = format_issue_text_block(issue, comments_char_budget=15)

    assert "new" in text
    assert "medium middle comment" not in text
    assert "short old" not in text


@pytest.mark.unit
def test_format_issue_text_block_renders_resolution_signals_instead_of_description() -> None:
    issue = FetchedIssue(
        issue_key="TJC-400",
        summary="Outage escalated",
        issue_type="Bug",
        reporter="support",
        zendesk_tickets=[
            LinkedZendeskTicket(
                ticket_id="47322",
                subject="Major outage reported",
                description="Entire platform down for all customers.",
                status="solved",
                priority="urgent",
                resolution_summary=ZendeskResolutionSummary(
                    initial_impact="All agents blocked from quoting.",
                    latest_status="Third-party DNS outage resolved; service restored.",
                    resolution_hints="Temporary external DNS provider outage.",
                    open_risks="(none)",
                ),
            ),
        ],
    )

    text = format_issue_text_block(issue)

    assert "Zendesk resolution signals:" in text
    assert "Initial impact: All agents blocked" in text
    assert "Latest status: Third-party DNS outage resolved" in text
    assert "Resolution hints: Temporary external DNS" in text
    assert "Entire platform down for all customers." not in text
    assert "Description:\nEntire platform" not in text


@pytest.mark.unit
def test_format_issue_text_block_falls_back_to_description_without_resolution_summary() -> None:
    issue = FetchedIssue(
        issue_key="TJC-401",
        summary="Login issue",
        issue_type="Bug",
        reporter="support",
        zendesk_tickets=[
            LinkedZendeskTicket(
                ticket_id="88",
                subject="Cannot sign in",
                description="MFA loop after password reset.",
                status="open",
                priority="normal",
            ),
        ],
    )

    text = format_issue_text_block(issue)

    assert "Zendesk resolution signals:" not in text
    assert "Description:\nMFA loop after password reset." in text


@pytest.mark.unit
def test_format_issue_text_block_dedupes_repeated_resolution_hints_across_tickets() -> None:
    shared_hints = "Temporary third-party DNS outage; recovered."
    issue = FetchedIssue(
        issue_key="TJC-402",
        summary="Related tickets",
        issue_type="Bug",
        reporter="support",
        zendesk_tickets=[
            LinkedZendeskTicket(
                ticket_id="1",
                subject="Re-opened outage",
                description="raw one",
                status="open",
                priority="high",
                resolution_summary=ZendeskResolutionSummary(
                    initial_impact="Peak outage language.",
                    latest_status="Recovered.",
                    resolution_hints=shared_hints,
                    open_risks="(none)",
                ),
            ),
            LinkedZendeskTicket(
                ticket_id="2",
                subject="Follow-up outage",
                description="raw two",
                status="solved",
                priority="normal",
                resolution_summary=ZendeskResolutionSummary(
                    initial_impact="Different opening.",
                    latest_status="Also recovered.",
                    resolution_hints=shared_hints,
                    open_risks="(none)",
                ),
            ),
        ],
    )

    text = format_issue_text_block(issue)

    assert text.count(shared_hints) == 1
    assert "Resolution hints: (same as above)" in text


@pytest.mark.unit
def test_format_issue_text_block_collapses_near_identical_resolution_summaries() -> None:
    summary = ZendeskResolutionSummary(
        initial_impact="Severe outage reported.",
        latest_status="Vendor incident cleared.",
        resolution_hints="External vendor outage.",
        open_risks="(none)",
    )
    issue = FetchedIssue(
        issue_key="TJC-403",
        summary="Duplicate escalations",
        issue_type="Bug",
        reporter="support",
        zendesk_tickets=[
            LinkedZendeskTicket(
                ticket_id="10",
                subject="Escalation A",
                description="long raw a",
                status="open",
                priority="urgent",
                resolution_summary=summary,
            ),
            LinkedZendeskTicket(
                ticket_id="11",
                subject="Escalation B",
                description="long raw b",
                status="solved",
                priority="high",
                resolution_summary=summary.model_copy(),
            ),
        ],
    )

    text = format_issue_text_block(issue)

    assert text.count("Zendesk resolution signals:") == 1
    assert "identical to a prior linked ticket" in text


@pytest.mark.unit
def test_format_issue_text_block_omits_near_identical_summary_with_shared_subject_prefix() -> None:
    issue = FetchedIssue(
        issue_key="TJC-404",
        summary="Related outage tickets",
        issue_type="Bug",
        reporter="support",
        zendesk_tickets=[
            LinkedZendeskTicket(
                ticket_id="20",
                subject="Outage: login broken",
                description="raw one",
                status="solved",
                priority="urgent",
                resolution_summary=ZendeskResolutionSummary(
                    initial_impact="Peak outage language.",
                    latest_status="Service restored after vendor fix.",
                    resolution_hints="Temporary third-party DNS outage.",
                    open_risks="(none)",
                ),
            ),
            LinkedZendeskTicket(
                ticket_id="21",
                subject="Outage: login broken (re-opened)",
                description="raw two",
                status="open",
                priority="high",
                resolution_summary=ZendeskResolutionSummary(
                    initial_impact="Different opening impact.",
                    latest_status="Service restored after vendor fix.",
                    resolution_hints="Temporary third-party DNS outage.",
                    open_risks="(none)",
                ),
            ),
        ],
    )

    text = format_issue_text_block(issue)

    assert text.count("Zendesk resolution signals:") == 1
    assert "identical to a prior linked ticket" in text


@pytest.mark.unit
def test_format_issue_text_block_omits_problem_id_linked_near_identical_summary() -> None:
    issue = FetchedIssue(
        issue_key="TJC-405",
        summary="Problem and incident tickets",
        issue_type="Bug",
        reporter="support",
        zendesk_tickets=[
            LinkedZendeskTicket(
                ticket_id="100",
                subject="Platform outage",
                description="raw problem",
                status="solved",
                priority="urgent",
                resolution_summary=ZendeskResolutionSummary(
                    initial_impact="Major outage.",
                    latest_status="Recovered.",
                    resolution_hints="External vendor incident.",
                    open_risks="(none)",
                ),
            ),
            LinkedZendeskTicket(
                ticket_id="101",
                subject="Follow-up on outage",
                description="raw incident",
                status="open",
                priority="normal",
                problem_id="100",
                resolution_summary=ZendeskResolutionSummary(
                    initial_impact="Customer follow-up.",
                    latest_status="Recovered.",
                    resolution_hints="External vendor incident.",
                    open_risks="(none)",
                ),
            ),
        ],
    )

    text = format_issue_text_block(issue)

    assert text.count("Zendesk resolution signals:") == 1
    assert "identical to a prior linked ticket" in text


@pytest.mark.unit
def test_format_issue_text_block_renders_zendesk_image_within_resolution_signals_block() -> None:
    issue = FetchedIssue(
        issue_key="TJC-500",
        summary="Outage with screenshot",
        issue_type="Bug",
        reporter="support",
        zendesk_tickets=[
            LinkedZendeskTicket(
                ticket_id="47322",
                subject="Major outage reported",
                description="Entire platform down.",
                status="solved",
                priority="urgent",
                resolution_summary=ZendeskResolutionSummary(
                    initial_impact="All agents blocked.",
                    latest_status="Recovered after vendor fix.",
                    resolution_hints="Temporary DNS outage.",
                    open_risks="(none)",
                ),
            ),
        ],
    )
    contexts = [
        ImageContext(
            attachment_id="zendesk:47322:https://z/only.png",
            filename="zendesk-only.png",
            transcript="404 Not Found",
            summary="White page with 404 heading.",
        ),
    ]

    text = format_issue_text_block(issue, image_contexts=contexts)

    signals_idx = text.index("Zendesk resolution signals:")
    attachment_idx = text.index("[Zendesk ticket #47322 attachment: zendesk-only.png]")
    assert attachment_idx > signals_idx
    assert "Summary:\nWhite page with 404 heading." in text
    assert "404 Not Found" not in text


@pytest.mark.unit
def test_format_issue_text_block_renders_zendesk_image_after_description_fallback() -> None:
    issue = FetchedIssue(
        issue_key="TJC-501",
        summary="Login issue",
        issue_type="Bug",
        reporter="support",
        zendesk_tickets=[
            LinkedZendeskTicket(
                ticket_id="88",
                subject="Cannot sign in",
                description="MFA loop after password reset.",
                status="open",
                priority="normal",
            ),
        ],
    )
    contexts = [
        ImageContext(
            attachment_id="zendesk:88:att-1",
            filename="login-error.png",
            summary="Red MFA error banner.",
        ),
    ]

    text = format_issue_text_block(issue, image_contexts=contexts)

    assert "Zendesk resolution signals:" not in text
    assert "Description:\nMFA loop after password reset." in text
    assert "[Zendesk ticket #88 attachment: login-error.png]" in text
    assert "Summary:\nRed MFA error banner." in text


@pytest.mark.unit
def test_format_issue_text_block_renders_zendesk_image_soft_failure_within_ticket_block() -> None:
    issue = FetchedIssue(
        issue_key="TJC-502",
        summary="Crash screenshot",
        issue_type="Bug",
        reporter="support",
        zendesk_tickets=[
            LinkedZendeskTicket(
                ticket_id="47322",
                subject="Crash report",
                description="App closes on launch.",
                status="open",
                priority="high",
            ),
        ],
    )
    contexts = [
        ImageContext(
            attachment_id="zendesk:47322:https://z/huge.png",
            filename="huge.png",
            extraction_failure="exceeds size limit",
        ),
    ]

    text = format_issue_text_block(issue, image_contexts=contexts)

    assert (
        "[Zendesk ticket #47322 attachment: extraction unavailable — exceeds size limit]"
        in text
    )


@pytest.mark.unit
def test_format_issue_text_block_keeps_zendesk_images_when_resolution_summary_omitted() -> None:
    summary = ZendeskResolutionSummary(
        initial_impact="Peak outage.",
        latest_status="Recovered.",
        resolution_hints="Vendor fix.",
        open_risks="(none)",
    )
    issue = FetchedIssue(
        issue_key="TJC-503",
        summary="Duplicate summaries",
        issue_type="Bug",
        reporter="support",
        zendesk_tickets=[
            LinkedZendeskTicket(
                ticket_id="10",
                subject="Escalation A",
                description="raw a",
                status="open",
                priority="urgent",
                resolution_summary=summary,
            ),
            LinkedZendeskTicket(
                ticket_id="11",
                subject="Escalation B",
                description="raw b",
                status="solved",
                priority="high",
                resolution_summary=summary.model_copy(),
            ),
        ],
    )
    contexts = [
        ImageContext(
            attachment_id="zendesk:11:https://z/unique.png",
            filename="ticket-11-only.png",
            summary="Unique screenshot for ticket 11.",
        ),
    ]

    text = format_issue_text_block(issue, image_contexts=contexts)

    assert "identical to a prior linked ticket" in text
    assert "[Zendesk ticket #11 attachment: ticket-11-only.png]" in text
    assert "Unique screenshot for ticket 11." in text
