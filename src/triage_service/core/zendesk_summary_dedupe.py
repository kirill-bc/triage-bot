"""Cross-ticket Zendesk resolution-summary dedupe heuristics."""

from __future__ import annotations

from dataclasses import dataclass, field

from triage_service.adapters.jira_issue_fetcher import (
    LinkedZendeskTicket,
    ZendeskResolutionSummary,
)

_MIN_SUBJECT_PREFIX_LEN = 12


def _normalize_dedupe_key(text: str) -> str:
    return " ".join(text.strip().lower().split())


def _resolution_summary_fingerprint(summary: ZendeskResolutionSummary) -> str:
    parts = (
        summary.initial_impact,
        summary.latest_status,
        summary.resolution_hints,
        summary.open_risks,
    )
    return _normalize_dedupe_key("|".join(parts))


def _core_summary_fingerprint(summary: ZendeskResolutionSummary) -> str:
    return _normalize_dedupe_key(
        f"{summary.latest_status}|{summary.resolution_hints}",
    )


def _subject_prefix_key(subject: str) -> str:
    return _normalize_dedupe_key(subject)


def shares_subject_prefix(left: str, right: str, *, min_len: int = _MIN_SUBJECT_PREFIX_LEN) -> bool:
    """Return True when normalized subjects share a meaningful common prefix."""
    left_key = _subject_prefix_key(left)
    right_key = _subject_prefix_key(right)
    if not left_key or not right_key:
        return False
    prefix_len = 0
    for left_char, right_char in zip(left_key, right_key, strict=False):
        if left_char != right_char:
            break
        prefix_len += 1
    return prefix_len >= min_len


@dataclass
class ZendeskSummaryDedupeState:
    seen_exact_fingerprints: set[str] = field(default_factory=set)
    seen_hints: set[str] = field(default_factory=set)
    rendered: list[tuple[LinkedZendeskTicket, ZendeskResolutionSummary]] = field(
        default_factory=list,
    )


def _matches_prior_summary(
    ticket: LinkedZendeskTicket,
    summary: ZendeskResolutionSummary,
    prior_ticket: LinkedZendeskTicket,
    prior_summary: ZendeskResolutionSummary,
) -> bool:
    exact_fp = _resolution_summary_fingerprint(summary)
    prior_exact_fp = _resolution_summary_fingerprint(prior_summary)
    if exact_fp == prior_exact_fp:
        return True

    core_fp = _core_summary_fingerprint(summary)
    prior_core_fp = _core_summary_fingerprint(prior_summary)
    if core_fp != prior_core_fp:
        return False

    if shares_subject_prefix(ticket.subject, prior_ticket.subject):
        return True

    problem_id = (ticket.problem_id or "").strip()
    prior_ticket_id = prior_ticket.ticket_id.strip()
    prior_problem_id = (prior_ticket.problem_id or "").strip()
    ticket_id = ticket.ticket_id.strip()
    if problem_id and problem_id == prior_ticket_id:
        return True
    if prior_problem_id and prior_problem_id == ticket_id:
        return True
    if problem_id and prior_problem_id and problem_id == prior_problem_id:
        return True
    return False


def resolution_summary_should_omit_as_duplicate(
    ticket: LinkedZendeskTicket,
    *,
    state: ZendeskSummaryDedupeState,
) -> bool:
    """Return True when this ticket's resolution signals duplicate a prior ticket."""
    summary = ticket.resolution_summary
    if summary is None:
        return False

    exact_fp = _resolution_summary_fingerprint(summary)
    if exact_fp in state.seen_exact_fingerprints:
        return True

    for prior_ticket, prior_summary in state.rendered:
        if _matches_prior_summary(ticket, summary, prior_ticket, prior_summary):
            return True
    return False


def record_rendered_resolution_summary(
    ticket: LinkedZendeskTicket,
    summary: ZendeskResolutionSummary,
    *,
    state: ZendeskSummaryDedupeState,
) -> None:
    """Track a ticket whose resolution signals were rendered."""
    exact_fp = _resolution_summary_fingerprint(summary)
    state.seen_exact_fingerprints.add(exact_fp)
    state.rendered.append((ticket, summary))
