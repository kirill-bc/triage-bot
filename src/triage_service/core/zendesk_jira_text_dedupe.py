"""Post-render guard: strip Zendesk summary sections that verbatim-echo Jira issue text."""

from __future__ import annotations

from triage_service.adapters.jira_issue_fetcher import (
    FetchedIssue,
    ZendeskResolutionSummary,
)

JIRA_ECHO_PLACEHOLDER = "(same as Jira issue above)"
_MIN_ECHO_SECTION_CHARS = 24


def _normalize_text(text: str) -> str:
    return " ".join(text.strip().lower().split())


def _jira_text_fields(issue: FetchedIssue) -> list[str]:
    fields: list[str] = [issue.summary]
    if issue.description:
        fields.append(issue.description)
    if issue.reproduction_steps:
        fields.append(issue.reproduction_steps)
    return fields


def _jira_text_corpus(issue: FetchedIssue) -> str:
    return _normalize_text("\n".join(_jira_text_fields(issue)))


def resolution_summary_section_echoes_jira(
    section_text: str,
    issue: FetchedIssue,
    *,
    min_chars: int = _MIN_ECHO_SECTION_CHARS,
) -> bool:
    """True when a summary section verbatim-echoes Jira summary/description/repro."""
    normalized = _normalize_text(section_text)
    if len(normalized) < min_chars:
        return False
    corpus = _jira_text_corpus(issue)
    if normalized in corpus:
        return True
    for field in _jira_text_fields(issue):
        if normalized == _normalize_text(field):
            return True
    return False


def _sanitize_section(section_text: str, issue: FetchedIssue) -> str:
    if resolution_summary_section_echoes_jira(section_text, issue):
        return JIRA_ECHO_PLACEHOLDER
    return section_text


def sanitize_resolution_summary_against_jira(
    summary: ZendeskResolutionSummary,
    issue: FetchedIssue,
) -> ZendeskResolutionSummary:
    """Replace resolution sections that still verbatim-echo Jira fields after summarization."""
    return ZendeskResolutionSummary(
        initial_impact=_sanitize_section(summary.initial_impact, issue),
        latest_status=_sanitize_section(summary.latest_status, issue),
        resolution_hints=_sanitize_section(summary.resolution_hints, issue),
        open_risks=_sanitize_section(summary.open_risks, issue),
    )
