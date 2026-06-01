"""CLI-friendly Zendesk enrichment summaries (mirrors image-context CLI output)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from triage_service.adapters.jira_issue_fetcher import (
    LinkedZendeskTicket,
    ZendeskResolutionSummary,
)


class ZendeskContextEnrichmentResult(BaseModel):
    """Zendesk fetch + optional summarization outcome from one triage run."""

    ticket_ids_requested: list[str] = Field(default_factory=list)
    tickets_fetched: int = Field(ge=0, default=0)
    tickets_summarized: int = Field(ge=0, default=0)
    total_summary_cost: float | None = Field(default=None, ge=0.0)
    fetch_failed: bool = False
    summary_failed: bool = False
    tickets: list[LinkedZendeskTicket] = Field(default_factory=list)


def _ticket_row(ticket: LinkedZendeskTicket) -> dict[str, object]:
    row: dict[str, object] = {
        "ticket_id": ticket.ticket_id,
        "subject": ticket.subject,
        "status": ticket.status,
        "priority": ticket.priority,
    }
    summary = ticket.resolution_summary
    if summary is not None:
        row["render_mode"] = "resolution_signals"
        row["resolution_signals"] = _resolution_signals_row(summary)
        return row
    row["render_mode"] = "subject_description"
    if ticket.description:
        row["description"] = ticket.description
    return row


def _resolution_signals_row(summary: ZendeskResolutionSummary) -> dict[str, str]:
    return {
        "initial_impact": summary.initial_impact,
        "latest_status": summary.latest_status,
        "resolution_hints": summary.resolution_hints,
        "open_risks": summary.open_risks,
    }


def build_cli_zendesk_context_summary(
    *,
    enabled: bool,
    enrichment: ZendeskContextEnrichmentResult | None,
) -> dict[str, object]:
    """Compact Zendesk enrichment summary for manual CLI smoke output."""
    if not enabled:
        return {"enabled": False}
    if enrichment is None:
        return {
            "enabled": True,
            "ticket_ids_requested": [],
            "tickets_fetched": 0,
            "tickets_summarized": 0,
            "tickets": [],
        }
    payload: dict[str, object] = {
        "enabled": True,
        "ticket_ids_requested": list(enrichment.ticket_ids_requested),
        "tickets_fetched": enrichment.tickets_fetched,
        "tickets_summarized": enrichment.tickets_summarized,
        "tickets": [_ticket_row(ticket) for ticket in enrichment.tickets],
    }
    if enrichment.total_summary_cost is not None:
        payload["total_summary_cost"] = enrichment.total_summary_cost
    if enrichment.fetch_failed:
        payload["fetch_failed"] = True
    if enrichment.summary_failed:
        payload["summary_failed"] = True
    return payload
