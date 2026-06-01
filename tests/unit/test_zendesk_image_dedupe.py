"""Unit tests for cross-source Jira vs Zendesk image deduplication."""

from __future__ import annotations

import hashlib

import pytest

from triage_service.adapters.jira_issue_fetcher import (
    AttachmentRef,
    LinkedZendeskTicket,
    ZendeskCommentRef,
    ZendeskImageRef,
)
from triage_service.core.zendesk_image_dedupe import (
    ZendeskImageDedupeSkip,
    collect_zendesk_image_refs_from_tickets,
    dedupe_zendesk_images_against_jira,
    match_zendesk_image_to_jira,
)


@pytest.mark.unit
def test_match_zendesk_image_skips_on_exact_jira_filename() -> None:
    zendesk = ZendeskImageRef(url="https://cdn.example/blobid0.png", filename="blobid0.png")
    jira = [
        AttachmentRef(
            id="10001",
            filename="blobid0.png",
            mime_type="image/png",
            size_bytes=1200,
            inline=True,
        ),
    ]
    decision = match_zendesk_image_to_jira(zendesk, jira)
    assert decision.skip is True
    assert decision.skip_reason == "jira_filename_match"
    assert decision.matched_jira_attachment_id == "10001"


@pytest.mark.unit
def test_match_zendesk_image_filename_match_is_case_insensitive() -> None:
    zendesk = ZendeskImageRef(
        url="https://cdn.example/IMAGE-20260219-160437.PNG",
        filename="IMAGE-20260219-160437.PNG",
    )
    jira = [
        AttachmentRef(
            id="att-2",
            filename="image-20260219-160437.png",
            mime_type="image/png",
            inline=True,
        ),
    ]
    decision = match_zendesk_image_to_jira(zendesk, jira)
    assert decision.skip is True
    assert decision.skip_reason == "jira_filename_match"


@pytest.mark.unit
def test_match_zendesk_image_skips_on_mime_and_size_within_tolerance() -> None:
    zendesk = ZendeskImageRef(
        url="https://zendesk.com/attachments/token/1",
        filename="screenshot.png",
        mime_type="image/png",
        size_bytes=10_000,
    )
    jira = [
        AttachmentRef(
            id="jira-1",
            filename="different-name.png",
            mime_type="image/png",
            size_bytes=10_200,
            inline=True,
        ),
    ]
    decision = match_zendesk_image_to_jira(zendesk, jira, size_tolerance_bytes=512)
    assert decision.skip is True
    assert decision.skip_reason == "jira_mime_size_match"
    assert decision.matched_jira_attachment_id == "jira-1"


@pytest.mark.unit
def test_match_zendesk_image_keeps_when_mime_matches_but_size_outside_tolerance() -> None:
    zendesk = ZendeskImageRef(
        url="https://zendesk.com/attachments/token/2",
        filename="other.png",
        mime_type="image/png",
        size_bytes=10_000,
    )
    jira = [
        AttachmentRef(
            id="jira-2",
            filename="unrelated.jpg",
            mime_type="image/png",
            size_bytes=50_000,
            inline=True,
        ),
    ]
    decision = match_zendesk_image_to_jira(zendesk, jira, size_tolerance_bytes=512)
    assert decision.skip is False
    assert decision.skip_reason is None


@pytest.mark.unit
def test_match_zendesk_image_skips_on_content_hash_when_provided() -> None:
    payload = b"same-image-bytes"
    digest = hashlib.sha256(payload).hexdigest()
    zendesk = ZendeskImageRef(url="https://zendesk.com/inline/1", filename="a.png")
    jira = [
        AttachmentRef(
            id="jira-hash",
            filename="totally-different.gif",
            mime_type="image/gif",
            inline=True,
        ),
    ]
    decision = match_zendesk_image_to_jira(
        zendesk,
        jira,
        zendesk_content_hash=digest,
        jira_content_hashes={"jira-hash": digest},
    )
    assert decision.skip is True
    assert decision.skip_reason == "jira_content_hash_match"


@pytest.mark.unit
def test_match_zendesk_image_keeps_when_no_jira_image_matches() -> None:
    zendesk = ZendeskImageRef(url="https://zendesk.com/x.png", filename="x.png")
    jira = [
        AttachmentRef(
            id="doc-1",
            filename="report.pdf",
            mime_type="application/pdf",
            inline=False,
        ),
    ]
    decision = match_zendesk_image_to_jira(zendesk, jira)
    assert decision.skip is False


@pytest.mark.unit
def test_dedupe_partitions_kept_and_skipped_with_audit_rows() -> None:
    tickets = [
        LinkedZendeskTicket(
            ticket_id="47322",
            subject="Outage",
            description_image_refs=[
                ZendeskImageRef(url="https://z/1", filename="blobid0.png"),
                ZendeskImageRef(url="https://z/2", filename="unique-only.png"),
            ],
        ),
    ]
    jira = [
        AttachmentRef(
            id="100",
            filename="blobid0.png",
            mime_type="image/png",
            inline=True,
        ),
    ]
    refs = collect_zendesk_image_refs_from_tickets(tickets)
    kept, skipped = dedupe_zendesk_images_against_jira(refs, jira)
    assert len(kept) == 1
    assert kept[0][1].filename == "unique-only.png"
    assert len(skipped) == 1
    assert isinstance(skipped[0], ZendeskImageDedupeSkip)
    assert skipped[0].ticket_id == "47322"
    assert skipped[0].skip_reason == "jira_filename_match"
    assert skipped[0].filename == "blobid0.png"


@pytest.mark.unit
def test_collect_zendesk_image_refs_includes_comment_images() -> None:
    tickets = [
        LinkedZendeskTicket(
            ticket_id="99",
            subject="S",
            comments=[
                ZendeskCommentRef(
                    comment_id="1",
                    body="see image",
                    public=True,
                    image_refs=[
                        ZendeskImageRef(url="https://z/cmt.png", filename="cmt.png"),
                    ],
                ),
            ],
        ),
    ]
    refs = collect_zendesk_image_refs_from_tickets(tickets)
    assert len(refs) == 1
    assert refs[0][0] == "99"
    assert refs[0][1].filename == "cmt.png"
