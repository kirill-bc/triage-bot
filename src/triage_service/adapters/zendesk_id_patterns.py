"""Shared Zendesk ticket id URL and short-token patterns for Jira field and body parsing."""

from __future__ import annotations

import re

ZENDESK_TICKET_URL_PATTERN = re.compile(
    r"https?://[A-Za-z0-9.-]*zendesk\.com/(?:agent/)?tickets/(\d+)",
    re.IGNORECASE,
)
ZENDESK_TICKET_SHORT_PATTERN = re.compile(r"\bZD[-\s#:]*(\d+)\b", re.IGNORECASE)

ZENDESK_TICKET_ID_PATTERNS = (ZENDESK_TICKET_URL_PATTERN, ZENDESK_TICKET_SHORT_PATTERN)
