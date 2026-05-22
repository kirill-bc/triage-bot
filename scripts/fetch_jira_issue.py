#!/usr/bin/env python3
"""Smoke-fetch one Jira issue by key using repo settings (.env or environment).

Zendesk ticket ids are read from Jira custom fields (Zendesk Ticket IDs and
Imported Zendesk Ticket IDs), then ticket details are fetched from Zendesk API
when credentials are configured.

Example (from repository root):

    .venv/bin/python scripts/fetch_jira_issue.py TJC-123
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

from pydantic import ValidationError

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from triage_service.adapters.jira_issue_fetcher import (  # noqa: E402
    FetchedIssue,
    JiraIssueFetchError,
    JiraIssueFetcher,
)
from triage_service.adapters.zendesk_ticket_fetcher import (  # noqa: E402
    ZendeskTicketFetchError,
    ZendeskTicketFetcher,
)
from triage_service.core.settings import AppSettings, load_settings  # noqa: E402


def build_fetch_output(issue: FetchedIssue, *, settings: AppSettings) -> dict[str, object]:
    """Assemble Jira issue JSON plus linked Zendesk ticket metadata."""
    payload: dict[str, object] = issue.model_dump()
    zendesk = ZendeskTicketFetcher(settings)
    ticket_ids = issue.zendesk_ticket_ids or zendesk.collect_linked_ticket_ids(issue)
    payload["linked_zendesk_ticket_ids"] = ticket_ids
    zendesk_error: str | None = None
    if not zendesk.credentials_configured:
        payload["zendesk_tickets"] = []
        if ticket_ids:
            payload["zendesk_fetch_skipped_reason"] = (
                "Zendesk credentials missing: set ZENDESK_BASE_URL, ZENDESK_API_TOKEN, "
                "and ZENDESK_USER_EMAIL or ZENDESK_AGENT_EMAIL."
            )
    elif ticket_ids:
        try:
            tickets = zendesk.fetch_tickets_by_ids(ticket_ids)
            payload["zendesk_tickets"] = [ticket.model_dump() for ticket in tickets]
        except ZendeskTicketFetchError as exc:
            zendesk_error = str(exc)
            payload["zendesk_tickets"] = []
    else:
        payload["zendesk_tickets"] = []
    if zendesk_error:
        payload["zendesk_fetch_error"] = zendesk_error
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch a Jira issue via REST and print normalized fields as JSON, "
            "including Zendesk tickets linked via Jira custom fields."
        ),
    )
    parser.add_argument(
        "issue_key",
        help="Jira issue key, e.g. TJC-123",
    )
    args = parser.parse_args(argv)

    dotenv_path = _ROOT / ".env"
    try:
        settings = load_settings(
            env_file=dotenv_path if dotenv_path.is_file() else None,
        )
    except ValidationError as exc:
        print(f"Settings error: {exc}", file=sys.stderr)
        return 2

    fetcher = JiraIssueFetcher(settings)
    try:
        issue = fetcher.fetch(args.issue_key.strip(), run_id=str(uuid.uuid4()))
    except JiraIssueFetchError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(json.dumps(build_fetch_output(issue, settings=settings), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
