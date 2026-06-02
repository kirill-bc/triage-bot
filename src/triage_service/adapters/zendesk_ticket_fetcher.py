"""Optional Zendesk ticket enrichment for Jira triage context."""

from __future__ import annotations

import base64
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from triage_service.adapters.jira_issue_fetcher import (
    FetchedIssue,
    LinkedZendeskTicket,
    ZendeskCommentRef,
    ZendeskImageRef,
    zendesk_comment_newest_first_sort_key,
)
from triage_service.adapters.zendesk_id_patterns import ZENDESK_TICKET_ID_PATTERNS
from triage_service.adapters.zendesk_oauth import (
    ZendeskOAuthClient,
    ZendeskOAuthError,
    build_zendesk_oauth_client,
)
from triage_service.core.settings import AppSettings

LOGGER = logging.getLogger(__name__)

_ZENDESK_MARKDOWN_IMAGE_RE = re.compile(
    r"!\[[^\]]*\]\((https?://[^)\s]+)\)",
    re.IGNORECASE,
)
_ZENDESK_HTML_IMAGE_RE = re.compile(
    r"""<img\b[^>]*\bsrc=["'](https?://[^"']+)["']""",
    re.IGNORECASE,
)
_IMAGE_MIME_PREFIX = "image/"
_FILENAME_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
_MAX_TICKET_DESCRIPTION_CHARS = 2000
# Shared Zendesk CDNs (exact host match only — never *.zendesk.com suffixes).
_ZENDESK_IMAGE_CDN_HOSTS = frozenset(
    {
        "cdn.zendesk.com",
        "static.zdassets.com",
    },
)


def is_trusted_zendesk_image_url(url: str, *, zendesk_base_url: str | None) -> bool:
    """Return True when auth may be sent for this Zendesk image URL.

    Only the configured tenant host and explicit Zendesk CDN hosts are trusted.
    Other ``*.zendesk.com`` tenants must not receive credentials from inline URLs.
    """
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"}:
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    configured_base = str(zendesk_base_url or "").strip().rstrip("/")
    if not configured_base:
        return False
    configured_host = (urlparse(configured_base).hostname or "").lower()
    if configured_host and host == configured_host:
        return True
    return host in _ZENDESK_IMAGE_CDN_HOSTS


def extract_zendesk_ticket_ids(texts: Iterable[str | None]) -> list[str]:
    """Collect unique Zendesk ticket ids from free-form text snippets."""
    found: list[str] = []
    seen: set[str] = set()
    for raw in texts:
        if raw is None:
            continue
        text = str(raw)
        for pattern in ZENDESK_TICKET_ID_PATTERNS:
            for match in pattern.finditer(text):
                ticket_id = str(match.group(1)).strip()
                if ticket_id and ticket_id not in seen:
                    seen.add(ticket_id)
                    found.append(ticket_id)
    return found


def extract_zendesk_inline_image_urls(text: str) -> list[str]:
    """Collect unique inline image URLs from Zendesk markdown/HTML comment or description text."""
    if not text.strip():
        return []
    found: list[str] = []
    seen: set[str] = set()
    for pattern in (_ZENDESK_MARKDOWN_IMAGE_RE, _ZENDESK_HTML_IMAGE_RE):
        for match in pattern.finditer(text):
            url = str(match.group(1)).strip()
            if url and url not in seen:
                seen.add(url)
                found.append(url)
    return found


def _filename_from_image_url(url: str) -> str | None:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    name_values = query.get("name")
    if name_values:
        candidate = str(name_values[0]).strip()
        if candidate:
            return candidate
    path_name = parsed.path.rsplit("/", 1)[-1].strip()
    if path_name and "." in path_name:
        return path_name
    return None


def _is_zendesk_image_attachment(
    *,
    filename: str,
    mime_type: str | None,
) -> bool:
    mime = (mime_type or "").strip().lower()
    if mime.startswith(_IMAGE_MIME_PREFIX):
        return True
    lowered = filename.lower()
    return any(lowered.endswith(ext) for ext in _FILENAME_IMAGE_EXTENSIONS)


def _parse_zendesk_attachment_ref(raw: Any) -> ZendeskImageRef | None:
    if not isinstance(raw, dict):
        return None
    attachment_id_raw = raw.get("id")
    if attachment_id_raw is None:
        return None
    attachment_id = str(attachment_id_raw).strip()
    if not attachment_id:
        return None
    filename = str(raw.get("file_name") or raw.get("filename") or attachment_id).strip()
    content_url_raw = raw.get("content_url") or raw.get("mapped_content_url")
    if not isinstance(content_url_raw, str) or not content_url_raw.strip():
        return None
    mime_raw = raw.get("content_type") or raw.get("mime_type")
    mime_type = str(mime_raw).strip() if isinstance(mime_raw, str) and mime_raw.strip() else None
    if not _is_zendesk_image_attachment(filename=filename, mime_type=mime_type):
        return None
    size_raw = raw.get("size")
    size_bytes: int | None = None
    if isinstance(size_raw, int):
        size_bytes = size_raw
    elif isinstance(size_raw, str) and size_raw.strip().isdigit():
        size_bytes = int(size_raw.strip())
    return ZendeskImageRef(
        url=content_url_raw.strip(),
        filename=filename or None,
        attachment_id=attachment_id,
        mime_type=mime_type,
        size_bytes=size_bytes,
        source="comment_attachment",
    )


def _inline_body_image_refs(
    body: str,
    *,
    source: str,
) -> list[ZendeskImageRef]:
    refs: list[ZendeskImageRef] = []
    for url in extract_zendesk_inline_image_urls(body):
        refs.append(
            ZendeskImageRef(
                url=url,
                filename=_filename_from_image_url(url),
                source=source,
            ),
        )
    return refs


def _merge_zendesk_image_refs(*groups: Iterable[ZendeskImageRef]) -> list[ZendeskImageRef]:
    merged: list[ZendeskImageRef] = []
    seen_urls: set[str] = set()
    seen_attachment_ids: set[str] = set()
    for group in groups:
        for ref in group:
            url = ref.url.strip()
            attachment_id = (ref.attachment_id or "").strip()
            if url in seen_urls:
                continue
            if attachment_id and attachment_id in seen_attachment_ids:
                continue
            seen_urls.add(url)
            if attachment_id:
                seen_attachment_ids.add(attachment_id)
            merged.append(ref)
    return merged


def _append_unique_ids(found: list[str], seen: set[str], ids: Iterable[str]) -> None:
    for raw in ids:
        ticket_id = str(raw).strip()
        if ticket_id and ticket_id not in seen:
            seen.add(ticket_id)
            found.append(ticket_id)


class ZendeskTicketFetchError(RuntimeError):
    """Raised when an individual Zendesk ticket fetch fails."""


@dataclass(frozen=True, slots=True)
class ZendeskTicketFetchFailureRecord:
    """Per-ticket Zendesk fetch failure collected during soft-fail batch fetch."""

    ticket_id: str
    failure: str


@dataclass(frozen=True, slots=True)
class ZendeskTicketsFetchResult:
    """Linked Zendesk tickets fetched plus per-ticket soft failures."""

    tickets: list[LinkedZendeskTicket]
    failures: list[ZendeskTicketFetchFailureRecord] = field(default_factory=list)


def _failure_code_from_fetch_error(exc: ZendeskTicketFetchError) -> str:
    message = str(exc).lower()
    http_match = re.search(r"http (\d{3})", message)
    if http_match:
        return f"http_{http_match.group(1)}"
    if "missing" in message:
        return "invalid_response"
    return "fetch_error"


class ZendeskTicketFetcher:
    """Loads linked Zendesk ticket summaries when optional credentials are configured."""

    def __init__(
        self,
        settings: AppSettings,
        *,
        client: httpx.Client | None = None,
        oauth_client: ZendeskOAuthClient | None = None,
    ) -> None:
        self._settings = settings
        self._client = client
        self._oauth_client = oauth_client
        if settings.triage_zendesk_enable_oauth and self._oauth_client is None:
            self._oauth_client = build_zendesk_oauth_client(settings)

    @property
    def credentials_configured(self) -> bool:
        if self._settings.triage_zendesk_enable_oauth:
            return bool(
                self._settings.zendesk_oauth_configured
                and self._oauth_client is not None,
            )
        return bool(
            self._settings.resolve_zendesk_base_url()
            and self._settings.zendesk_user_email
            and self._settings.zendesk_api_token,
        )

    @property
    def enabled(self) -> bool:
        if not self._settings.triage_zendesk_context_enabled:
            return False
        return self.credentials_configured

    def collect_linked_ticket_ids(self, issue: FetchedIssue) -> list[str]:
        """Union Zendesk ids from Jira custom fields and issue body text without duplicates."""
        ticket_ids, _ = self.collect_linked_ticket_ids_with_stats(issue)
        return ticket_ids

    def collect_linked_ticket_ids_with_stats(
        self,
        issue: FetchedIssue,
    ) -> tuple[list[str], int]:
        """Return linked ticket ids and count of duplicates removed during union/cap."""
        found: list[str] = []
        seen: set[str] = set()
        custom_ids = list(issue.zendesk_ticket_ids or [])
        _append_unique_ids(found, seen, custom_ids)
        body_ids = extract_zendesk_ticket_ids(
            (issue.summary, issue.description, issue.reproduction_steps),
        )
        max_tickets = self._settings.triage_zendesk_max_tickets
        if custom_ids:
            _append_unique_ids(found, seen, body_ids)
            dropped = max(0, len(found) - max_tickets)
            capped = found[:max_tickets]
            deduped = len(custom_ids) + len(body_ids) - len(found) + dropped
            return capped, deduped
        capped = body_ids[:max_tickets]
        cap_dropped = max(0, len(body_ids) - len(capped))
        return capped, cap_dropped

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
        return self.fetch_tickets_by_ids_with_failures(ticket_ids).tickets

    def fetch_tickets_by_ids(self, ticket_ids: list[str]) -> list[LinkedZendeskTicket]:
        return self.fetch_tickets_by_ids_with_failures(ticket_ids).tickets

    def fetch_tickets_by_ids_with_failures(
        self,
        ticket_ids: list[str],
    ) -> ZendeskTicketsFetchResult:
        if not self.credentials_configured:
            return ZendeskTicketsFetchResult(tickets=[])
        if self._client is not None:
            return self._fetch_tickets_with_failures(self._client, ticket_ids)
        timeout = httpx.Timeout(self._settings.zendesk_http_timeout_seconds)
        with httpx.Client(timeout=timeout) as client:
            return self._fetch_tickets_with_failures(client, ticket_ids)

    def _fetch_tickets_with_failures(
        self,
        client: httpx.Client,
        ticket_ids: list[str],
    ) -> ZendeskTicketsFetchResult:
        tickets: list[LinkedZendeskTicket] = []
        failures: list[ZendeskTicketFetchFailureRecord] = []
        for ticket_id in ticket_ids:
            try:
                tickets.append(self._fetch_ticket(client, ticket_id))
            except ZendeskTicketFetchError as exc:
                failures.append(
                    ZendeskTicketFetchFailureRecord(
                        ticket_id=ticket_id,
                        failure=_failure_code_from_fetch_error(exc),
                    ),
                )
                LOGGER.warning(
                    "Zendesk ticket fetch failed; continuing with remaining tickets",
                    extra={"ticket_id": ticket_id},
                    exc_info=True,
                )
        return ZendeskTicketsFetchResult(tickets=tickets, failures=failures)

    def fetch_image_bytes(self, image_ref: ZendeskImageRef, *, run_id: str) -> bytes:
        """Download binary content for a discovered Zendesk image reference."""
        _ = run_id
        url = image_ref.url.strip()
        if not url:
            raise ZendeskTicketFetchError("Zendesk image URL is required for content fetch.")
        headers = self._image_fetch_headers(url)
        if self._client is not None:
            response = self._client.get(url, headers=headers, follow_redirects=True)
            return self._image_bytes_from_response(response)
        timeout = httpx.Timeout(self._settings.zendesk_http_timeout_seconds)
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            response = client.get(url, headers=headers)
            return self._image_bytes_from_response(response)

    def _image_fetch_headers(self, url: str) -> dict[str, str]:
        headers: dict[str, str] = {"Accept": "*/*"}
        if is_trusted_zendesk_image_url(
            url,
            zendesk_base_url=self._settings.resolve_zendesk_base_url(),
        ):
            headers["Authorization"] = self._auth_header()
        return headers

    def _image_bytes_from_response(self, response: httpx.Response) -> bytes:
        if response.status_code >= 400:
            snippet = response.text[:300]
            raise ZendeskTicketFetchError(
                f"Zendesk image fetch failed with HTTP {response.status_code}: {snippet}",
            )
        return response.content

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
        parsed = self._parse_ticket(ticket, ticket_id=ticket_id)
        comments = self._fetch_comments(client, ticket_id)
        return parsed.model_copy(update={"comments": comments})

    def _fetch_comments(self, client: httpx.Client, ticket_id: str) -> list[ZendeskCommentRef]:
        max_comments = self._settings.triage_zendesk_max_comments_per_ticket
        if max_comments <= 0:
            return []
        try:
            response = client.get(
                self._comments_url(ticket_id, per_page=max_comments),
                headers={
                    "Authorization": self._auth_header(),
                    "Accept": "application/json",
                },
            )
            if response.status_code >= 400:
                snippet = response.text[:300]
                raise ZendeskTicketFetchError(
                    f"Zendesk comments fetch failed with HTTP {response.status_code}: {snippet}",
                )
            payload = response.json()
            raw_comments = payload.get("comments")
            if not isinstance(raw_comments, list):
                raise ZendeskTicketFetchError("Zendesk comments response missing 'comments' list.")
            parsed: list[ZendeskCommentRef] = []
            for item in raw_comments:
                comment = self._parse_comment(item)
                if comment is not None:
                    parsed.append(comment)
            parsed.sort(key=zendesk_comment_newest_first_sort_key, reverse=True)
            return parsed[:max_comments]
        except ZendeskTicketFetchError:
            LOGGER.warning(
                "Zendesk comment fetch failed; continuing without comments",
                extra={"ticket_id": ticket_id},
                exc_info=True,
            )
            return []

    def _parse_comment(self, raw: Any) -> ZendeskCommentRef | None:
        if not isinstance(raw, dict):
            return None
        comment_id_raw = raw.get("id")
        if comment_id_raw is None:
            return None
        comment_id = str(comment_id_raw).strip()
        if not comment_id:
            return None
        body_raw = raw.get("body")
        body = str(body_raw).strip() if isinstance(body_raw, str) else ""
        public_raw = raw.get("public")
        public = bool(public_raw) if isinstance(public_raw, bool) else False
        created_raw = raw.get("created_at")
        created_at = (
            str(created_raw).strip()
            if isinstance(created_raw, str) and created_raw.strip()
            else None
        )
        attachment_refs: list[ZendeskImageRef] = []
        attachments_raw = raw.get("attachments")
        if isinstance(attachments_raw, list):
            for item in attachments_raw:
                attachment_ref = _parse_zendesk_attachment_ref(item)
                if attachment_ref is not None:
                    attachment_refs.append(attachment_ref)
        image_refs = _merge_zendesk_image_refs(
            _inline_body_image_refs(body, source="comment_inline_body"),
            attachment_refs,
        )
        return ZendeskCommentRef(
            comment_id=comment_id,
            body=body,
            public=public,
            created_at=created_at,
            image_refs=image_refs,
        )

    def _parse_ticket(self, raw: dict[str, Any], *, ticket_id: str) -> LinkedZendeskTicket:
        rid = str(raw.get("id") or ticket_id).strip() or ticket_id
        subject = str(raw.get("subject") or "").strip() or "(no subject)"
        description_raw = raw.get("description")
        description_text = (
            str(description_raw).strip()
            if isinstance(description_raw, str) and description_raw.strip()
            else ""
        )
        description = description_text[:_MAX_TICKET_DESCRIPTION_CHARS] if description_text else None
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
        description_image_refs = _inline_body_image_refs(
            description_text,
            source="ticket_description_inline",
        )
        problem_id_raw = raw.get("problem_id")
        problem_id: str | None = None
        if problem_id_raw is not None:
            problem_id_text = str(problem_id_raw).strip()
            if problem_id_text:
                problem_id = problem_id_text
        return LinkedZendeskTicket(
            ticket_id=rid,
            subject=subject,
            description=description,
            status=status,
            priority=priority,
            problem_id=problem_id,
            url=url,
            description_image_refs=description_image_refs,
        )

    def _base_url(self) -> str:
        base = self._settings.resolve_zendesk_base_url()
        if not base:
            raise ZendeskTicketFetchError("ZENDESK_BASE_URL is required.")
        return base

    def _ticket_url(self, ticket_id: str) -> str:
        return f"{self._base_url()}/api/v2/tickets/{ticket_id}.json"

    def _comments_url(self, ticket_id: str, *, per_page: int) -> str:
        base = f"{self._base_url()}/api/v2/tickets/{ticket_id}/comments.json"
        return (
            f"{base}?include_inline_images=true"
            f"&sort_order=desc&per_page={per_page}"
        )

    def _auth_header(self) -> str:
        if self._settings.triage_zendesk_enable_oauth:
            if self._oauth_client is None:
                raise ZendeskTicketFetchError(
                    "Zendesk OAuth is enabled but the OAuth client is not configured.",
                )
            try:
                access_token = self._oauth_client.get_access_token()
            except ZendeskOAuthError as exc:
                raise ZendeskTicketFetchError(str(exc)) from exc
            return f"Bearer {access_token}"
        email = str(self._settings.zendesk_user_email or "").strip()
        token = str(self._settings.zendesk_api_token or "").strip()
        if not email or not token:
            raise ZendeskTicketFetchError(
                "Zendesk credentials required "
                "(ZENDESK_USER_EMAIL or ZENDESK_AGENT_EMAIL, ZENDESK_API_TOKEN).",
            )
        encoded = base64.b64encode(f"{email}/token:{token}".encode("utf-8")).decode("ascii")
        return f"Basic {encoded}"
