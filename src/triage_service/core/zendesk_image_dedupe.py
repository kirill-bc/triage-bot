"""Cross-source deduplication of Zendesk images against Jira attachments before vision."""

from __future__ import annotations

from pydantic import BaseModel

from triage_service.adapters.jira_issue_fetcher import (
    AttachmentRef,
    LinkedZendeskTicket,
    ZendeskImageRef,
)

_IMAGE_MIME_PREFIX = "image/"
_FILENAME_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
_DEFAULT_SIZE_TOLERANCE_BYTES = 512

SKIP_REASON_FILENAME = "jira_filename_match"
SKIP_REASON_MIME_SIZE = "jira_mime_size_match"
SKIP_REASON_CONTENT_HASH = "jira_content_hash_match"


class ZendeskImageDedupeDecision(BaseModel):
    """Whether a Zendesk image should be skipped because Jira already has it."""

    skip: bool = False
    skip_reason: str | None = None
    matched_jira_attachment_id: str | None = None


class ZendeskImageDedupeSkip(BaseModel):
    """Skipped Zendesk image with reason for audit metadata."""

    ticket_id: str
    url: str
    filename: str | None = None
    skip_reason: str
    matched_jira_attachment_id: str | None = None


def _normalize_filename(filename: str | None) -> str | None:
    if not filename:
        return None
    cleaned = filename.strip().lower()
    if "/" in cleaned:
        cleaned = cleaned.rsplit("/", 1)[-1]
    return cleaned or None


def _is_image_attachment(ref: AttachmentRef) -> bool:
    mime = (ref.mime_type or "").strip().lower()
    if mime.startswith(_IMAGE_MIME_PREFIX):
        return True
    name = ref.filename.lower()
    return any(name.endswith(ext) for ext in _FILENAME_IMAGE_EXTENSIONS)


def _is_image_zendesk_ref(ref: ZendeskImageRef) -> bool:
    mime = (ref.mime_type or "").strip().lower()
    if mime.startswith(_IMAGE_MIME_PREFIX):
        return True
    filename = (ref.filename or "").lower()
    return any(filename.endswith(ext) for ext in _FILENAME_IMAGE_EXTENSIONS) or bool(filename)


def _jira_image_attachments(attachments: list[AttachmentRef]) -> list[AttachmentRef]:
    return [ref for ref in attachments if _is_image_attachment(ref)]


def _filename_match(zendesk: ZendeskImageRef, jira: AttachmentRef) -> bool:
    z_name = _normalize_filename(zendesk.filename)
    j_name = _normalize_filename(jira.filename)
    return bool(z_name and j_name and z_name == j_name)


def _mime_size_match(
    zendesk: ZendeskImageRef,
    jira: AttachmentRef,
    *,
    size_tolerance_bytes: int,
) -> bool:
    z_mime = (zendesk.mime_type or "").strip().lower()
    j_mime = (jira.mime_type or "").strip().lower()
    if not z_mime or not j_mime or z_mime != j_mime:
        return False
    if zendesk.size_bytes is None or jira.size_bytes is None:
        return False
    return abs(zendesk.size_bytes - jira.size_bytes) <= size_tolerance_bytes


def _content_hash_match(
    jira: AttachmentRef,
    *,
    zendesk_content_hash: str | None,
    jira_content_hashes: dict[str, str] | None,
) -> bool:
    if not zendesk_content_hash or not jira_content_hashes:
        return False
    jira_hash = jira_content_hashes.get(jira.id)
    return bool(jira_hash and jira_hash == zendesk_content_hash)


def match_zendesk_image_to_jira(
    zendesk: ZendeskImageRef,
    jira_attachments: list[AttachmentRef],
    *,
    size_tolerance_bytes: int = _DEFAULT_SIZE_TOLERANCE_BYTES,
    zendesk_content_hash: str | None = None,
    jira_content_hashes: dict[str, str] | None = None,
) -> ZendeskImageDedupeDecision:
    """Return skip decision using filename, MIME+size, then optional content hash tiers."""
    for jira in _jira_image_attachments(jira_attachments):
        if _filename_match(zendesk, jira):
            return ZendeskImageDedupeDecision(
                skip=True,
                skip_reason=SKIP_REASON_FILENAME,
                matched_jira_attachment_id=jira.id,
            )
    for jira in _jira_image_attachments(jira_attachments):
        if _mime_size_match(zendesk, jira, size_tolerance_bytes=size_tolerance_bytes):
            return ZendeskImageDedupeDecision(
                skip=True,
                skip_reason=SKIP_REASON_MIME_SIZE,
                matched_jira_attachment_id=jira.id,
            )
    for jira in _jira_image_attachments(jira_attachments):
        if _content_hash_match(
            jira,
            zendesk_content_hash=zendesk_content_hash,
            jira_content_hashes=jira_content_hashes,
        ):
            return ZendeskImageDedupeDecision(
                skip=True,
                skip_reason=SKIP_REASON_CONTENT_HASH,
                matched_jira_attachment_id=jira.id,
            )
    return ZendeskImageDedupeDecision()


def collect_zendesk_image_refs_from_tickets(
    tickets: list[LinkedZendeskTicket],
) -> list[tuple[str, ZendeskImageRef]]:
    """Flatten description and comment image refs with ticket id for traceability."""
    refs: list[tuple[str, ZendeskImageRef]] = []
    seen_urls: set[str] = set()
    for ticket in tickets:
        for image_ref in ticket.description_image_refs + [
            ref for comment in ticket.comments for ref in comment.image_refs
        ]:
            if not _is_image_zendesk_ref(image_ref):
                continue
            if image_ref.url in seen_urls:
                continue
            seen_urls.add(image_ref.url)
            refs.append((ticket.ticket_id, image_ref))
    return refs


def dedupe_zendesk_images_against_jira(
    zendesk_refs: list[tuple[str, ZendeskImageRef]],
    jira_attachments: list[AttachmentRef],
    *,
    size_tolerance_bytes: int = _DEFAULT_SIZE_TOLERANCE_BYTES,
    zendesk_content_hashes: dict[str, str] | None = None,
    jira_content_hashes: dict[str, str] | None = None,
) -> tuple[list[tuple[str, ZendeskImageRef]], list[ZendeskImageDedupeSkip]]:
    """Partition Zendesk images into vision candidates and Jira-duplicate skips."""
    kept: list[tuple[str, ZendeskImageRef]] = []
    skipped: list[ZendeskImageDedupeSkip] = []
    for ticket_id, image_ref in zendesk_refs:
        z_hash = (zendesk_content_hashes or {}).get(image_ref.url)
        decision = match_zendesk_image_to_jira(
            image_ref,
            jira_attachments,
            size_tolerance_bytes=size_tolerance_bytes,
            zendesk_content_hash=z_hash,
            jira_content_hashes=jira_content_hashes,
        )
        if decision.skip and decision.skip_reason:
            skipped.append(
                ZendeskImageDedupeSkip(
                    ticket_id=ticket_id,
                    url=image_ref.url,
                    filename=image_ref.filename,
                    skip_reason=decision.skip_reason,
                    matched_jira_attachment_id=decision.matched_jira_attachment_id,
                ),
            )
        else:
            kept.append((ticket_id, image_ref))
    return kept, skipped
