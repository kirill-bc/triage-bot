"""Unit tests for ADF media / mediaSingle attachment-id discovery."""

from __future__ import annotations

import pytest

from triage_service.adapters.jira_issue_fetcher import collect_media_attachment_ids_from_adf


@pytest.mark.unit
def test_collect_media_ids_from_media_single_node() -> None:
    adf = {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "mediaSingle",
                "content": [
                    {
                        "type": "media",
                        "attrs": {"type": "file", "id": "inline-uuid-1", "collection": ""},
                    },
                ],
            },
        ],
    }
    assert collect_media_attachment_ids_from_adf(adf) == ["inline-uuid-1"]


@pytest.mark.unit
def test_collect_media_ids_from_nested_paragraph_and_multiple_media_singles() -> None:
    adf = {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": "See screenshots"}],
            },
            {
                "type": "mediaSingle",
                "content": [
                    {"type": "media", "attrs": {"id": "first-inline"}},
                ],
            },
            {
                "type": "mediaSingle",
                "content": [
                    {"type": "media", "attrs": {"id": "second-inline"}},
                ],
            },
        ],
    }
    assert collect_media_attachment_ids_from_adf(adf) == ["first-inline", "second-inline"]


@pytest.mark.unit
def test_collect_media_ids_from_standalone_media_node() -> None:
    adf = {
        "type": "doc",
        "content": [
            {"type": "media", "attrs": {"id": "standalone-media-id"}},
        ],
    }
    assert collect_media_attachment_ids_from_adf(adf) == ["standalone-media-id"]


@pytest.mark.unit
def test_collect_media_ids_skips_nodes_without_id_and_deduplicates() -> None:
    adf = {
        "type": "doc",
        "content": [
            {"type": "media", "attrs": {"id": "dup-id"}},
            {"type": "media", "attrs": {"id": "dup-id"}},
            {"type": "media", "attrs": {}},
        ],
    }
    assert collect_media_attachment_ids_from_adf(adf) == ["dup-id"]


@pytest.mark.unit
def test_collect_media_ids_returns_empty_for_none_or_plain_text() -> None:
    assert collect_media_attachment_ids_from_adf(None) == []
    assert collect_media_attachment_ids_from_adf("plain text description") == []
