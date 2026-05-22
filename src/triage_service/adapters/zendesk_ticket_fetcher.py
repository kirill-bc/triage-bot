"""Optional Zendesk ticket enrichment for Jira triage context."""

from __future__ import annotations

import base64
import re
from collections.abc import Iterable
from typing import Any

import httpx

from triage_service.adapters.jira_issue_fetcher import FetchedIssue, LinkedZendeskTicket
from triage_service.core.settings import AppSettings

_ZENDESK_URL_RE = re.compile(
    r"https?://[A-Za-z0-9.-]*zendesk\.com/(?:agent/)?tickets/(\d+)",
    re.IGNORECASE,
)
_ZENDESK_SHORT_RE = re.compile(r"\bZD[-\s#:]*(\d+)\b", re.IGNORECASE)


def extract_zendesk_ticket_ids(texts: Iterable[str | None]) -> list[str]:
    """Collect unique Zendesk ticket ids from free-form text snippets."""
    found: list[str] = []
    seen: set[str] = set()
    for raw in texts:
        if raw is None:
            continue
        text = str(raw)
        for pattern in (_ZENDESK_URL_RE, _ZENDESK_SHORT_RE):
            for match in pattern.finditer(text):
                ticket_id = str(match.group(1)).strip()
                if ticket_id and ticket_id not in seen:
                    seen.add(ticket_id)
                    found.append(ticket_id)
    return found


def _append_unique_ids(found: list[str], seen: set[str], ids: Iterable[str]) -> None:
    for raw in ids:
        ticket_id = str(raw).strip()
        if ticket_id and ticket_id not in seen:
            seen.add(ticket_id)
            found.append(ticket_id)


class ZendeskTicketFetchError(RuntimeError):
    """Raised when an individual Zendesk ticket fetch fails."""


class ZendeskTicketFetcher:
    """Loads linked Zendesk ticket summaries when optional credentials are configured."""

    def __init__(self, settings: AppSettings, *, client: httpx.Client | None = None) -> None:
        self._settings = settings
        self._client = client

    @property
    def credentials_configured(self) -> bool:
        return bool(
            self._settings.zendesk_base_url
            and self._settings.zendesk_user_email
            and self._settings.zendesk_api_token,
        )

    @property
    def enabled(self) -> bool:
        if not self._settings.triage_zendesk_context_enabled:
            return False
        return self.credentials_configured

    def collect_linked_ticket_ids(self, issue: FetchedIssue) -> list[str]:
        """Use all Zendesk ids from Jira custom fields when present; else parse issue body text."""
        if issue.zendesk_ticket_ids:
            return list(issue.zendesk_ticket_ids)
        found: list[str] = []
        seen: set[str] = set()
        _append_unique_ids(
            found,
            seen,
            extract_zendesk_ticket_ids(
                (issue.summary, issue.description, issue.reproduction_steps),
            ),
        )
        return found[: self._settings.triage_zendesk_max_tickets]

    def fetch_linked_tickets(
        self,
        issue: FetchedIssue,
        *,
        run_id: str,
    ) -> list[LinkedZendeskTicket]:
        _ = run_id
        if not self.enabled:
            return []
        ticket_ids = self.collect_linked_ticket_ids(issue)
        if not ticket_ids:
            return []
        return self.fetch_tickets_by_ids(ticket_ids)

    def fetch_tickets_by_ids(self, ticket_ids: list[str]) -> list[LinkedZendeskTicket]:
        if not self.credentials_configured:
            return []
        tickets: list[LinkedZendeskTicket] = []
        if self._client is not None:
            for ticket_id in ticket_ids:
                tickets.append(self._fetch_ticket(self._client, ticket_id))
            return tickets
        timeout = httpx.Timeout(self._settings.zendesk_http_timeout_seconds)
        with httpx.Client(timeout=timeout) as client:
            for ticket_id in ticket_ids:
                tickets.append(self._fetch_ticket(client, ticket_id))
        return tickets

    def _fetch_ticket(self, client: httpx.Client, ticket_id: str) -> LinkedZendeskTicket:
        response = client.get(
            self._ticket_url(ticket_id),
            headers={
                "Authorization": self._auth_header(),
                "Accept": "application/json",
            },
        )
        if response.status_code >= 400:
            snippet = response.text[:300]
            raise ZendeskTicketFetchError(
                f"Zendesk ticket fetch failed with HTTP {response.status_code}: {snippet}",
            )
        payload = response.json()
        ticket = payload.get("ticket")
        if not isinstance(ticket, dict):
            raise ZendeskTicketFetchError("Zendesk ticket response missing 'ticket' object.")
        return self._parse_ticket(ticket, ticket_id=ticket_id)

    def _parse_ticket(self, raw: dict[str, Any], *, ticket_id: str) -> LinkedZendeskTicket:
        rid = str(raw.get("id") or ticket_id).strip() or ticket_id
        subject = str(raw.get("subject") or "").strip() or "(no subject)"
        description_raw = raw.get("description")
        description = (
            str(description_raw).strip()[:2000]
            if isinstance(description_raw, str) and description_raw.strip()
            else None
        )
        status_raw = raw.get("status")
        if isinstance(status_raw, str) and status_raw.strip():
            status = str(status_raw).strip()
        else:
            status = None
        priority_raw = raw.get("priority")
        priority = (
            str(priority_raw).strip()
            if isinstance(priority_raw, str) and priority_raw.strip()
            else None
        )
        url = f"{self._base_url()}/agent/tickets/{rid}"
        return LinkedZendeskTicket(
            ticket_id=rid,
            subject=subject,
            description=description,
            status=status,
            priority=priority,
            url=url,
        )

    def _base_url(self) -> str:
        base = str(self._settings.zendesk_base_url or "").strip().rstrip("/")
        if not base:
            raise ZendeskTicketFetchError("ZENDESK_BASE_URL is required.")
        return base

    def _ticket_url(self, ticket_id: str) -> str:
        return f"{self._base_url()}/api/v2/tickets/{ticket_id}.json"

    def _auth_header(self) -> str:
        email = str(self._settings.zendesk_user_email or "").strip()
        token = str(self._settings.zendesk_api_token or "").strip()
        if not email or not token:
            raise ZendeskTicketFetchError(
                "Zendesk credentials required "
                "(ZENDESK_USER_EMAIL or ZENDESK_AGENT_EMAIL, ZENDESK_API_TOKEN).",
            )
        encoded = base64.b64encode(f"{email}/token:{token}".encode("utf-8")).decode("ascii")
        return f"Basic {encoded}"
