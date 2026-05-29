"""Unit tests for issue text block formatting."""

from __future__ import annotations

import pytest

from triage_service.adapters.jira_issue_fetcher import CommentRef, FetchedIssue
from triage_service.core.issue_text_block import format_issue_text_block


@pytest.mark.unit
def test_format_issue_text_block_includes_comments_when_present() -> None:
    issue = FetchedIssue(
        issue_key="TJC-300",
        summary="Login fails",
        description="Cannot log in on Safari",
        issue_type="Bug",
        priority="P2",
        reporter="Alice",
        comments=[
            CommentRef(
                id="1",
                author="Bob",
                created="2026-05-29T11:00:00.000+0000",
                body="I can reproduce this issue",
                attachment_ids=[],
            ),
        ],
    )

    text = format_issue_text_block(issue, comments_char_budget=500)

    assert "Comments:" in text
    assert "Bob" in text
    assert "I can reproduce this issue" in text


@pytest.mark.unit
def test_format_issue_text_block_truncates_oldest_comments_when_over_budget() -> None:
    issue = FetchedIssue(
        issue_key="TJC-301",
        summary="Login fails",
        description="Cannot log in on Safari",
        issue_type="Bug",
        priority="P2",
        reporter="Alice",
        comments=[
            CommentRef(
                id="1",
                author="Old",
                created="2026-05-29T10:00:00.000+0000",
                body="old comment should be dropped first",
                attachment_ids=[],
            ),
            CommentRef(
                id="2",
                author="New",
                created="2026-05-29T11:00:00.000+0000",
                body="newest comment should remain",
                attachment_ids=[],
            ),
        ],
    )

    text = format_issue_text_block(issue, comments_char_budget=40)

    assert "Comments:" in text
    assert "newest comment should remain" in text
    assert "old comment should be dropped first" not in text


@pytest.mark.unit
def test_format_issue_text_block_shows_none_when_no_comments() -> None:
    issue = FetchedIssue(
        issue_key="TJC-303",
        summary="Login fails",
        description="Cannot log in on Safari",
        issue_type="Bug",
        priority="P2",
        reporter="Alice",
        comments=[],
    )

    text = format_issue_text_block(issue, comments_char_budget=500)

    assert "Comments:\n(none)" in text


@pytest.mark.unit
def test_format_issue_text_block_shows_omitted_when_budget_zero() -> None:
    issue = FetchedIssue(
        issue_key="TJC-304",
        summary="Login fails",
        description="Cannot log in on Safari",
        issue_type="Bug",
        priority="P2",
        reporter="Alice",
        comments=[
            CommentRef(
                id="1",
                author="Bob",
                created="2026-05-29T11:00:00.000+0000",
                body="I can reproduce this issue",
                attachment_ids=[],
            ),
        ],
    )

    text = format_issue_text_block(issue, comments_char_budget=0)

    assert "Comments:\n(omitted by comment budget)" in text
    assert "I can reproduce this issue" not in text


@pytest.mark.unit
def test_format_issue_text_block_shows_omitted_when_all_comments_exceed_budget() -> None:
    issue = FetchedIssue(
        issue_key="TJC-305",
        summary="Login fails",
        description="Cannot log in on Safari",
        issue_type="Bug",
        priority="P2",
        reporter="Alice",
        comments=[
            CommentRef(
                id="1",
                author="Bob",
                created="2026-05-29T11:00:00.000+0000",
                body="this comment is too long for the budget",
                attachment_ids=[],
            ),
        ],
    )

    text = format_issue_text_block(issue, comments_char_budget=5)

    assert "Comments:\n(omitted by comment budget)" in text
    assert "this comment is too long for the budget" not in text


@pytest.mark.unit
def test_format_issue_text_block_keeps_contiguous_newest_comment_suffix() -> None:
    issue = FetchedIssue(
        issue_key="TJC-302",
        summary="Login fails",
        description="Cannot log in on Safari",
        issue_type="Bug",
        priority="P2",
        reporter="Alice",
        comments=[
            CommentRef(
                id="1",
                author="Old",
                created="2026-05-29T10:00:00.000+0000",
                body="short old",
                attachment_ids=[],
            ),
            CommentRef(
                id="2",
                author="Mid",
                created="2026-05-29T10:30:00.000+0000",
                body="medium middle comment",
                attachment_ids=[],
            ),
            CommentRef(
                id="3",
                author="New",
                created="2026-05-29T11:00:00.000+0000",
                body="new",
                attachment_ids=[],
            ),
        ],
    )

    text = format_issue_text_block(issue, comments_char_budget=15)

    assert "new" in text
    assert "medium middle comment" not in text
    assert "short old" not in text
