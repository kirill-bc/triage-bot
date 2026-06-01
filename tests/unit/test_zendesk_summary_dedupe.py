"""Unit tests for post-summarization Zendesk cross-ticket dedupe heuristics."""

from __future__ import annotations

import pytest

from triage_service.adapters.jira_issue_fetcher import (
    LinkedZendeskTicket,
    ZendeskResolutionSummary,
)
from triage_service.core.zendesk_summary_dedupe import (
    ZendeskSummaryDedupeState,
    record_rendered_resolution_summary,
    resolution_summary_should_omit_as_duplicate,
    shares_subject_prefix,
)


@pytest.mark.unit
def test_shares_subject_prefix_requires_meaningful_overlap() -> None:
    assert shares_subject_prefix("Outage: login broken", "Outage: login broken (re-opened)")
    assert not shares_subject_prefix("Login issue", "Billing error")


@pytest.mark.unit
def test_resolution_summary_omits_exact_fingerprint_duplicate() -> None:
    summary = ZendeskResolutionSummary(
        initial_impact="Peak outage.",
        latest_status="Recovered.",
        resolution_hints="Vendor DNS.",
        open_risks="(none)",
    )
    state = ZendeskSummaryDedupeState()
    first = LinkedZendeskTicket(
        ticket_id="10",
        subject="Escalation A",
        resolution_summary=summary,
    )
    second = LinkedZendeskTicket(
        ticket_id="11",
        subject="Escalation B",
        resolution_summary=summary.model_copy(),
    )

    assert not resolution_summary_should_omit_as_duplicate(first, state=state)
    assert first.resolution_summary is not None
    record_rendered_resolution_summary(first, first.resolution_summary, state=state)
    assert resolution_summary_should_omit_as_duplicate(second, state=state)


@pytest.mark.unit
def test_resolution_summary_omits_near_identical_with_shared_subject_prefix() -> None:
    state = ZendeskSummaryDedupeState()
    first = LinkedZendeskTicket(
        ticket_id="20",
        subject="Outage: login broken",
        resolution_summary=ZendeskResolutionSummary(
            initial_impact="All users blocked at peak.",
            latest_status="Service restored after vendor fix.",
            resolution_hints="Temporary third-party DNS outage.",
            open_risks="(none)",
        ),
    )
    second = LinkedZendeskTicket(
        ticket_id="21",
        subject="Outage: login broken (re-opened)",
        resolution_summary=ZendeskResolutionSummary(
            initial_impact="Customer still seeing errors.",
            latest_status="Service restored after vendor fix.",
            resolution_hints="Temporary third-party DNS outage.",
            open_risks="(none)",
        ),
    )

    assert not resolution_summary_should_omit_as_duplicate(first, state=state)
    assert first.resolution_summary is not None
    record_rendered_resolution_summary(first, first.resolution_summary, state=state)
    assert resolution_summary_should_omit_as_duplicate(second, state=state)


@pytest.mark.unit
def test_resolution_summary_omits_when_problem_id_links_to_prior_ticket() -> None:
    state = ZendeskSummaryDedupeState()
    problem = LinkedZendeskTicket(
        ticket_id="100",
        subject="Platform outage",
        resolution_summary=ZendeskResolutionSummary(
            initial_impact="Major outage.",
            latest_status="Recovered.",
            resolution_hints="External vendor incident.",
            open_risks="(none)",
        ),
    )
    incident = LinkedZendeskTicket(
        ticket_id="101",
        subject="Follow-up on outage",
        problem_id="100",
        resolution_summary=ZendeskResolutionSummary(
            initial_impact="Customer follow-up.",
            latest_status="Recovered.",
            resolution_hints="External vendor incident.",
            open_risks="(none)",
        ),
    )

    assert not resolution_summary_should_omit_as_duplicate(problem, state=state)
    assert problem.resolution_summary is not None
    record_rendered_resolution_summary(problem, problem.resolution_summary, state=state)
    assert resolution_summary_should_omit_as_duplicate(incident, state=state)


@pytest.mark.unit
def test_resolution_summary_keeps_distinct_core_when_only_problem_id_links() -> None:
    state = ZendeskSummaryDedupeState()
    problem = LinkedZendeskTicket(
        ticket_id="200",
        subject="Platform outage",
        resolution_summary=ZendeskResolutionSummary(
            initial_impact="Major outage.",
            latest_status="Still degraded.",
            resolution_hints="Investigating internally.",
            open_risks="Retry queue backlog.",
        ),
    )
    incident = LinkedZendeskTicket(
        ticket_id="201",
        subject="Customer ping",
        problem_id="200",
        resolution_summary=ZendeskResolutionSummary(
            initial_impact="Customer follow-up.",
            latest_status="Fully recovered.",
            resolution_hints="Root cause fixed in deploy.",
            open_risks="(none)",
        ),
    )

    assert not resolution_summary_should_omit_as_duplicate(problem, state=state)
    assert problem.resolution_summary is not None
    record_rendered_resolution_summary(problem, problem.resolution_summary, state=state)
    assert not resolution_summary_should_omit_as_duplicate(incident, state=state)
