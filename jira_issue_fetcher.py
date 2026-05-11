"""Fetch Jira issue fields via REST API v3 by issue key."""

from __future__ import annotations

import base64
from typing import Any

import httpx
from pydantic import BaseModel

from settings import AppSettings


class FetchedIssue(BaseModel):
    """Normalized issue fields used by triage composition."""

    issue_key: str
    summary: str
    description: str | None = None
    issue_type: str
    priority: str | None = None
    reporter: str


class JiraIssueFetchError(RuntimeError):
    """Raised when configuration or HTTP prevents loading an issue."""


def _extract_text_from_adf(node: Any) -> str:
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        if "text" in node:
            return str(node["text"])
        parts: list[str] = []
        for child in node.get("content") or []:
            parts.append(_extract_text_from_adf(child))
        return "".join(parts)
    if isinstance(node, list):
        return "".join(_extract_text_from_adf(item) for item in node)
    return ""


def _normalize_description(raw: Any) -> str | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        stripped = raw.strip()
        return stripped if stripped else None
    if isinstance(raw, dict):
        text = _extract_text_from_adf(raw).strip()
        return text if text else None
    return None


def _reporter_label(reporter: dict[str, Any]) -> str:
    display = reporter.get("displayName")
    if isinstance(display, str) and display.strip():
        return display.strip()
    account = reporter.get("accountId")
    if isinstance(account, str) and account.strip():
        return account.strip()
    email = reporter.get("emailAddress")
    if isinstance(email, str) and email.strip():
        return email.strip()
    return ""


def _parse_issue_payload(payload: dict[str, Any]) -> FetchedIssue:
    key = str(payload["key"])
    fields = payload.get("fields") or {}
    summary = str(fields.get("summary") or "").strip()
    description = _normalize_description(fields.get("description"))
    issue_type_obj = fields.get("issuetype") or {}
    issue_type = str(issue_type_obj.get("name") or "").strip()
    priority_obj = fields.get("priority")
    if priority_obj is None:
        priority_name: str | None = None
    else:
        priority_name = str(priority_obj.get("name") or "").strip() or None
    reporter_obj = fields.get("reporter") or {}
    reporter = _reporter_label(reporter_obj)
    return FetchedIssue(
        issue_key=key,
        summary=summary,
        description=description,
        issue_type=issue_type,
        priority=priority_name,
        reporter=reporter,
    )


def _basic_auth_header(email: str, api_token: str) -> str:
    token_bytes = f"{email}:{api_token}".encode("utf-8")
    encoded = base64.b64encode(token_bytes).decode("ascii")
    return f"Basic {encoded}"


class JiraIssueFetcher:
    """Loads issue summary, description, type, priority, and reporter from Jira Cloud REST v3."""

    _FIELDS = "summary,description,issuetype,priority,reporter"

    def __init__(self, settings: AppSettings, *, client: httpx.Client | None = None) -> None:
        self._settings = settings
        self._client = client

    def fetch(self, issue_key: str) -> FetchedIssue:
        base = self._settings.jira_base_url
        if base is None or not str(base).strip():
            raise JiraIssueFetchError(
                "Jira base URL is required to fetch issues (set JIRA_BASE_URL).",
            )
        email = self._settings.jira_user_email
        if email is None or not str(email).strip():
            raise JiraIssueFetchError(
                "Jira user email is required for REST auth (set JIRA_USER_EMAIL).",
            )

        base_url = str(base).rstrip("/")
        url = f"{base_url}/rest/api/3/issue/{issue_key}"
        params = {"fields": self._FIELDS}
        headers = {
            "Authorization": _basic_auth_header(email, self._settings.jira_api_key),
            "Accept": "application/json",
        }

        if self._client is not None:
            return self._request(self._client, url, params, headers)

        with httpx.Client(timeout=30.0) as client:
            return self._request(client, url, params, headers)

    def _request(
        self,
        client: httpx.Client,
        url: str,
        params: dict[str, str],
        headers: dict[str, str],
    ) -> FetchedIssue:
        response = client.get(url, params=params, headers=headers)
        if response.is_error:
            snippet = response.text[:200]
            raise JiraIssueFetchError(
                f"Jira issue request failed with HTTP {response.status_code}: {snippet}",
            )
        payload = response.json()
        return _parse_issue_payload(payload)
