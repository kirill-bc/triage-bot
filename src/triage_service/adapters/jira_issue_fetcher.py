"""Fetch Jira issue fields via REST API v3 by issue key."""

from __future__ import annotations

import base64
import re
from typing import Any

import httpx
from pydantic import BaseModel, Field

from triage_service.adapters.jira_http_retry import (
    TransportRetriesExhausted,
    classify_transport_request_error,
    request_with_retries,
)
from triage_service.core.settings import AppSettings

_ATLASSIAN_GATEWAY = "https://api.atlassian.com/ex/jira"


class AttachmentRef(BaseModel):
    """Normalized Jira attachment metadata for triage."""

    id: str
    filename: str
    mime_type: str | None = None
    size_bytes: int | None = None
    inline: bool = False


class LinkedZendeskTicket(BaseModel):
    """Normalized Zendesk ticket context linked from a Jira issue."""

    ticket_id: str
    subject: str
    description: str | None = None
    status: str | None = None
    priority: str | None = None
    url: str | None = None


_ZENDESK_FIELD_URL_RE = re.compile(
    r"https?://[A-Za-z0-9.-]*zendesk\.com/(?:agent/)?tickets/(\d+)",
    re.IGNORECASE,
)
_ZENDESK_FIELD_SHORT_RE = re.compile(r"\bZD[-\s#:]*(\d+)\b", re.IGNORECASE)


def _text_from_jira_custom_field(raw: Any) -> str:
    if isinstance(raw, dict):
        return _extract_text_from_adf(raw).strip()
    if isinstance(raw, str):
        return raw.strip()
    return ""


def _append_digit_tokens(text: str, found: list[str], seen: set[str]) -> None:
    for token in re.split(r"[\s,;]+", text):
        cleaned = token.strip().strip("[]\"'")
        if cleaned.isdigit() and cleaned not in seen:
            seen.add(cleaned)
            found.append(cleaned)


def _append_pattern_ticket_ids(text: str, found: list[str], seen: set[str]) -> None:
    for pattern in (_ZENDESK_FIELD_URL_RE, _ZENDESK_FIELD_SHORT_RE):
        for match in pattern.finditer(text):
            ticket_id = str(match.group(1)).strip()
            if ticket_id and ticket_id not in seen:
                seen.add(ticket_id)
                found.append(ticket_id)


def parse_zendesk_ticket_ids_from_field_value(raw: Any) -> list[str]:
    """Parse Zendesk numeric ticket ids from a Jira custom field (text or ADF)."""
    if raw is None:
        return []
    if isinstance(raw, (int, float)):
        value = int(raw)
        return [str(value)] if value > 0 else []
    text = _text_from_jira_custom_field(raw)
    if not text:
        return []
    found: list[str] = []
    seen: set[str] = set()
    _append_digit_tokens(text, found, seen)
    _append_pattern_ticket_ids(text, found, seen)
    return found


def _merge_zendesk_ticket_ids(
    fields: dict[str, Any],
    *,
    field_ids: list[str],
) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for field_id in field_ids:
        if field_id not in fields:
            continue
        for ticket_id in parse_zendesk_ticket_ids_from_field_value(fields.get(field_id)):
            if ticket_id not in seen:
                seen.add(ticket_id)
                found.append(ticket_id)
    return found


def _parse_zendesk_ticket_count(fields: dict[str, Any], *, field_id: str | None) -> int | None:
    fid = (field_id or "").strip()
    if not fid or fid not in fields:
        return None
    raw = fields.get(fid)
    if isinstance(raw, (int, float)):
        return int(raw)
    if isinstance(raw, str) and raw.strip().isdigit():
        return int(raw.strip())
    return None


class FetchedIssue(BaseModel):
    """Normalized issue fields used by triage composition."""

    issue_key: str
    issue_id: str | None = None
    summary: str
    description: str | None = None
    reproduction_steps: str | None = None
    issue_type: str
    priority: str | None = None
    reporter: str
    reporter_account_id: str | None = None
    attachments: list[AttachmentRef] = Field(default_factory=list)
    zendesk_ticket_ids: list[str] = Field(default_factory=list)
    zendesk_ticket_count: int | None = None
    zendesk_tickets: list[LinkedZendeskTicket] = Field(default_factory=list)


class JiraIssueFetchError(RuntimeError):
    """Raised when configuration or HTTP prevents loading an issue."""

    def __init__(
        self,
        message: str,
        *,
        attempts: int | None = None,
        http_status: int | None = None,
        transport_timeout: bool | None = None,
        transport_error_kind: str | None = None,
    ) -> None:
        super().__init__(message)
        self.attempts = attempts
        self.http_status = http_status
        self.transport_timeout = transport_timeout
        self.transport_error_kind = transport_error_kind


def _adf_media_id_from_node(node: dict[str, Any]) -> str | None:
    if node.get("type") != "media":
        return None
    attrs = node.get("attrs") or {}
    media_id = attrs.get("id")
    if isinstance(media_id, str) and media_id.strip():
        return media_id.strip()
    return None


def _walk_adf_collect_media_ids(node: Any, found: list[str], seen: set[str]) -> None:
    if node is None:
        return
    if isinstance(node, dict):
        media_id = _adf_media_id_from_node(node)
        if media_id is not None and media_id not in seen:
            seen.add(media_id)
            found.append(media_id)
        for child in node.get("content") or []:
            _walk_adf_collect_media_ids(child, found, seen)
        return
    if isinstance(node, list):
        for item in node:
            _walk_adf_collect_media_ids(item, found, seen)


def collect_media_attachment_ids_from_adf(node: Any) -> list[str]:
    """Return attachment ids referenced by ``media`` / ``mediaSingle`` ADF nodes."""
    if node is None or isinstance(node, str):
        return []
    found: list[str] = []
    seen: set[str] = set()
    _walk_adf_collect_media_ids(node, found, seen)
    return found


_RENDERED_ATTACHMENT_ID_RE = re.compile(
    r"/(?:secure/attachment|attachment/content)/([A-Za-z0-9-]+)",
)


def collect_attachment_ids_from_rendered_description(rendered_description: Any) -> list[str]:
    """Extract attachment ids referenced in rendered description HTML URLs."""
    if not isinstance(rendered_description, str) or not rendered_description.strip():
        return []
    found: list[str] = []
    seen: set[str] = set()
    for match in _RENDERED_ATTACHMENT_ID_RE.finditer(rendered_description):
        attachment_id = match.group(1).strip()
        if attachment_id and attachment_id not in seen:
            seen.add(attachment_id)
            found.append(attachment_id)
    return found


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


def _reporter_account_id(reporter: dict[str, Any]) -> str | None:
    raw = reporter.get("accountId")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
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


def _extract_reproduction_steps(description: str | None) -> str | None:
    if description is None:
        return None
    text = description.strip()
    if not text:
        return None
    marker = re.search(
        r"(?is)\b(?:steps?\s+to\s+reproduce|reproduction\s+steps?)\b\s*:?\s*(.*)$",
        text,
    )
    if marker is None:
        return None
    extracted = marker.group(1).strip()
    return extracted if extracted else text


def _parse_attachment_ref(raw: dict[str, Any], *, inline: bool) -> AttachmentRef | None:
    raw_id = raw.get("id")
    if raw_id is None:
        return None
    attachment_id = str(raw_id).strip()
    if not attachment_id:
        return None
    filename = str(raw.get("filename") or "").strip() or attachment_id
    mime_raw = raw.get("mimeType")
    mime_type = str(mime_raw).strip() if isinstance(mime_raw, str) and mime_raw.strip() else None
    size_raw = raw.get("size")
    size_bytes: int | None = None
    if isinstance(size_raw, int):
        size_bytes = size_raw
    elif isinstance(size_raw, str) and size_raw.strip().isdigit():
        size_bytes = int(size_raw.strip())
    return AttachmentRef(
        id=attachment_id,
        filename=filename,
        mime_type=mime_type,
        size_bytes=size_bytes,
        inline=inline,
    )


def _parse_attachments(
    fields: dict[str, Any],
    *,
    description_raw: Any,
    rendered_description: Any,
) -> list[AttachmentRef]:
    inline_ids = set(collect_media_attachment_ids_from_adf(description_raw))
    inline_ids.update(
        collect_attachment_ids_from_rendered_description(rendered_description),
    )
    raw_attachments = fields.get("attachment")
    if not isinstance(raw_attachments, list):
        return []
    parsed: list[AttachmentRef] = []
    for item in raw_attachments:
        if not isinstance(item, dict):
            continue
        ref = _parse_attachment_ref(item, inline=False)
        if ref is None:
            continue
        parsed.append(
            ref.model_copy(update={"inline": ref.id in inline_ids}),
        )
    return parsed


def _parse_issue_payload(
    payload: dict[str, Any],
    *,
    reproduction_steps_field_id: str | None = None,
    zendesk_ticket_ids_field_ids: list[str] | None = None,
    zendesk_ticket_count_field_id: str | None = None,
) -> FetchedIssue:
    key = str(payload["key"])
    raw_issue_id = payload.get("id")
    issue_id: str | None = None
    if raw_issue_id is not None:
        stripped = str(raw_issue_id).strip()
        issue_id = stripped if stripped else None
    fields = payload.get("fields") or {}
    summary = str(fields.get("summary") or "").strip()
    description_raw = fields.get("description")
    description = _normalize_description(description_raw)
    rendered_fields = payload.get("renderedFields")
    rendered_description = (
        rendered_fields.get("description")
        if isinstance(rendered_fields, dict)
        else None
    )
    attachments = _parse_attachments(
        fields,
        description_raw=description_raw,
        rendered_description=rendered_description,
    )
    reproduction_steps: str | None = None
    repro_field_id = (reproduction_steps_field_id or "").strip()
    if repro_field_id and repro_field_id in fields:
        reproduction_steps = _normalize_description(fields.get(repro_field_id))
    if reproduction_steps is None:
        reproduction_steps = _extract_reproduction_steps(description)
    issue_type_obj = fields.get("issuetype") or {}
    issue_type = str(issue_type_obj.get("name") or "").strip()
    priority_obj = fields.get("priority")
    if priority_obj is None:
        priority_name: str | None = None
    else:
        priority_name = str(priority_obj.get("name") or "").strip() or None
    reporter_obj = fields.get("reporter") or {}
    reporter = _reporter_label(reporter_obj)
    reporter_aid = _reporter_account_id(reporter_obj)
    zendesk_ids = _merge_zendesk_ticket_ids(
        fields,
        field_ids=zendesk_ticket_ids_field_ids or [],
    )
    zendesk_count = _parse_zendesk_ticket_count(
        fields,
        field_id=zendesk_ticket_count_field_id,
    )
    return FetchedIssue(
        issue_key=key,
        issue_id=issue_id,
        summary=summary,
        description=description,
        reproduction_steps=reproduction_steps,
        issue_type=issue_type,
        priority=priority_name,
        reporter=reporter,
        reporter_account_id=reporter_aid,
        attachments=attachments,
        zendesk_ticket_ids=zendesk_ids,
        zendesk_ticket_count=zendesk_count,
    )


def _basic_auth_header(email: str, api_token: str) -> str:
    token_bytes = f"{email}:{api_token}".encode("utf-8")
    encoded = base64.b64encode(token_bytes).decode("ascii")
    return f"Basic {encoded}"


def _gateway_prefix(settings: AppSettings) -> str:
    cloud_raw = settings.jira_cloud_id
    if cloud_raw is None or not str(cloud_raw).strip():
        raise JiraIssueFetchError(
            "Jira REST URL requires JIRA_CLOUD_ID (Atlassian gateway).",
        )
    cloud_id = str(cloud_raw).strip()
    return f"{_ATLASSIAN_GATEWAY}/{cloud_id}"


def _issue_get_url(settings: AppSettings, issue_key: str) -> str:
    """REST v3 issue URL using Atlassian gateway ``JIRA_CLOUD_ID``."""
    return f"{_gateway_prefix(settings)}/rest/api/3/issue/{issue_key}"


def _attachment_content_url(settings: AppSettings, attachment_id: str) -> str:
    """REST v3 attachment binary URL using Atlassian gateway ``JIRA_CLOUD_ID``."""
    att_id = str(attachment_id).strip()
    if not att_id:
        raise JiraIssueFetchError("Attachment id is required for content fetch.")
    return f"{_gateway_prefix(settings)}/rest/api/3/attachment/content/{att_id}"


class JiraIssueFetcher:
    """Loads issue fields via Jira Cloud REST v3 through Atlassian gateway."""

    _BASE_FIELDS = (
        "summary",
        "description",
        "issuetype",
        "priority",
        "reporter",
        "attachment",
    )

    def __init__(self, settings: AppSettings, *, client: httpx.Client | None = None) -> None:
        self._settings = settings
        self._client = client

    def fetch(self, issue_key: str, *, run_id: str) -> FetchedIssue:
        _ = run_id
        url = _issue_get_url(self._settings, issue_key)
        params = {"fields": self._fields_param(), "expand": "renderedFields"}
        headers = {
            **self._auth_headers(),
            "Accept": "application/json",
        }

        if self._client is not None:
            return self._request_issue(self._client, url, params, headers)

        timeout = httpx.Timeout(self._settings.jira_http_timeout_seconds)
        with httpx.Client(timeout=timeout) as client:
            return self._request_issue(client, url, params, headers)

    def fetch_attachment_bytes(self, attachment_id: str, *, run_id: str) -> bytes:
        _ = run_id
        url = _attachment_content_url(self._settings, attachment_id)
        headers = {
            **self._auth_headers(),
            "Accept": "*/*",
        }
        # Jira defaults to 303 → media CDN; following without signed URLs often yields HTML.
        params = {"redirect": "false"}

        if self._client is not None:
            response = self._get_with_retries(
                self._client,
                url,
                headers=headers,
                params=params,
            )
            return self._attachment_bytes_from_response(response)

        timeout = httpx.Timeout(self._settings.jira_http_timeout_seconds)
        with httpx.Client(timeout=timeout) as client:
            response = self._get_with_retries(
                client,
                url,
                headers=headers,
                params=params,
            )
            return self._attachment_bytes_from_response(response)

    def _attachment_bytes_from_response(self, response: httpx.Response) -> bytes:
        if response.is_redirect:
            raise JiraIssueFetchError(
                "Jira attachment content returned HTTP "
                f"{response.status_code} redirect; expected 200 with binary "
                "(use redirect=false on the content URL).",
                http_status=response.status_code,
            )
        return response.content

    def _auth_headers(self) -> dict[str, str]:
        email = self._settings.jira_user_email
        if email is None or not str(email).strip():
            raise JiraIssueFetchError(
                "Jira user email is required for REST auth (set JIRA_USER_EMAIL).",
            )
        return {
            "Authorization": _basic_auth_header(email, self._settings.jira_api_key),
        }

    def _fields_param(self) -> str:
        fields = list(self._BASE_FIELDS)
        repro_field_id = (self._settings.jira_reproduction_steps_field_id or "").strip()
        if repro_field_id:
            fields.append(repro_field_id)
        for field_id in self._zendesk_custom_field_ids():
            if field_id not in fields:
                fields.append(field_id)
        return ",".join(fields)

    def _zendesk_custom_field_ids(self) -> list[str]:
        out: list[str] = []
        for raw in (
            self._settings.jira_zendesk_ticket_ids_field_id,
            self._settings.jira_imported_zendesk_ticket_ids_field_id,
            self._settings.jira_zendesk_ticket_count_field_id,
        ):
            field_id = (raw or "").strip()
            if field_id:
                out.append(field_id)
        return out

    def _request_issue(
        self,
        client: httpx.Client,
        url: str,
        params: dict[str, str],
        headers: dict[str, str],
    ) -> FetchedIssue:
        response = self._get_with_retries(
            client,
            url,
            params=params,
            headers=headers,
        )
        payload = response.json()
        return _parse_issue_payload(
            payload,
            reproduction_steps_field_id=self._settings.jira_reproduction_steps_field_id,
            zendesk_ticket_ids_field_ids=[
                stripped
                for raw in (
                    self._settings.jira_zendesk_ticket_ids_field_id,
                    self._settings.jira_imported_zendesk_ticket_ids_field_id,
                )
                if (stripped := (raw or "").strip())
            ],
            zendesk_ticket_count_field_id=self._settings.jira_zendesk_ticket_count_field_id,
        )

    def _get_with_retries(
        self,
        client: httpx.Client,
        url: str,
        *,
        params: dict[str, str] | None = None,
        headers: dict[str, str],
    ) -> httpx.Response:
        try:
            response, attempts = request_with_retries(
                client,
                "GET",
                url,
                max_retries=self._settings.jira_http_max_retries,
                params=params,
                headers=headers,
            )
        except TransportRetriesExhausted as tre:
            timeout, kind = classify_transport_request_error(tre.cause)
            raise JiraIssueFetchError(
                f"Jira request failed after retries: {tre.cause}",
                attempts=tre.attempts,
                transport_timeout=timeout,
                transport_error_kind=kind,
            ) from tre.cause
        except httpx.RequestError as exc:
            timeout, kind = classify_transport_request_error(exc)
            raise JiraIssueFetchError(
                f"Jira request failed: {exc}",
                attempts=1,
                transport_timeout=timeout,
                transport_error_kind=kind,
            ) from exc
        if response.is_error:
            snippet = response.text[:200]
            raise JiraIssueFetchError(
                f"Jira request failed with HTTP {response.status_code}: {snippet}",
                attempts=attempts,
                http_status=response.status_code,
            )
        return response
