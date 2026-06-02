"""Unit tests for optional Zendesk ticket enrichment."""

from __future__ import annotations

import httpx
import pytest

from triage_service.adapters.jira_issue_fetcher import (
    FetchedIssue,
    ZendeskImageRef,
    parse_zendesk_ticket_ids_from_field_value,
)
from triage_service.adapters.zendesk_ticket_fetcher import (
    ZendeskTicketFetchError,
    ZendeskTicketFetcher,
    extract_zendesk_inline_image_urls,
    extract_zendesk_ticket_ids,
    is_trusted_zendesk_image_url,
)
from triage_service.core.settings import AppSettings


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> AppSettings:
    monkeypatch.setenv("JIRA_API_KEY", "jira-api-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-token")
    monkeypatch.setenv("TRIAGE_WEBHOOK_TOKEN", "triage-token")
    monkeypatch.setenv("TRIAGE_ZENDESK_CONTEXT_ENABLED", "true")
    monkeypatch.setenv("ZENDESK_BASE_URL", "https://acme.zendesk.com")
    monkeypatch.setenv("ZENDESK_USER_EMAIL", "agent@example.com")
    monkeypatch.setenv("ZENDESK_API_TOKEN", "zd-token")
    return AppSettings()


@pytest.mark.unit
def test_parse_zendesk_ticket_ids_from_field_value_multiline_and_urls() -> None:
    raw = "5001\n5002, 5003\nhttps://acme.zendesk.com/agent/tickets/5004"
    assert parse_zendesk_ticket_ids_from_field_value(raw) == [
        "5001",
        "5002",
        "5003",
        "5004",
    ]


@pytest.mark.unit
def test_parse_zendesk_ticket_ids_from_adf_multiparagraph_field() -> None:
    """Regression: ADF blocks must not concatenate into one numeric token (123+456 -> 123456)."""
    raw = {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": "123"}],
            },
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": "456"}],
            },
        ],
    }
    assert parse_zendesk_ticket_ids_from_field_value(raw) == ["123", "456"]


@pytest.mark.unit
def test_parse_zendesk_ticket_ids_from_adf_bullet_list_field() -> None:
    raw = {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "bulletList",
                "content": [
                    {
                        "type": "listItem",
                        "content": [
                            {
                                "type": "paragraph",
                                "content": [{"type": "text", "text": "789"}],
                            },
                        ],
                    },
                    {
                        "type": "listItem",
                        "content": [
                            {
                                "type": "paragraph",
                                "content": [{"type": "text", "text": "101112"}],
                            },
                        ],
                    },
                ],
            },
        ],
    }
    assert parse_zendesk_ticket_ids_from_field_value(raw) == ["789", "101112"]


@pytest.mark.unit
def test_extract_zendesk_ticket_ids_supports_urls_and_short_tokens() -> None:
    ids = extract_zendesk_ticket_ids(
        (
            "https://acme.zendesk.com/agent/tickets/12345",
            "duplicate zd-12345 should dedupe",
            "customer linked ZD #998877 for same case",
        ),
    )
    assert ids == ["12345", "998877"]


@pytest.mark.unit
def test_collect_linked_ticket_ids_unions_custom_fields_with_body_text_ids(
    settings: AppSettings,
) -> None:
    issue = FetchedIssue(
        issue_key="BC-9",
        summary="mentions ZD-999 in body only",
        issue_type="Bug",
        reporter="support",
        zendesk_ticket_ids=["5001", "5002"],
    )
    fetcher = ZendeskTicketFetcher(settings)
    assert fetcher.collect_linked_ticket_ids(issue) == ["5001", "5002", "999"]


@pytest.mark.unit
def test_collect_linked_ticket_ids_dedupes_ids_across_custom_fields_and_body(
    settings: AppSettings,
) -> None:
    issue = FetchedIssue(
        issue_key="BC-9",
        summary="duplicate ZD-5001 and extra ZD-777",
        description="also https://acme.zendesk.com/agent/tickets/5002",
        issue_type="Bug",
        reporter="support",
        zendesk_ticket_ids=["5001", "5002"],
    )
    fetcher = ZendeskTicketFetcher(settings)
    assert fetcher.collect_linked_ticket_ids(issue) == ["5001", "5002", "777"]


@pytest.mark.unit
def test_collect_linked_ticket_ids_caps_custom_field_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JIRA_API_KEY", "jira-api-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-token")
    monkeypatch.setenv("TRIAGE_WEBHOOK_TOKEN", "triage-token")
    monkeypatch.setenv("TRIAGE_ZENDESK_CONTEXT_ENABLED", "true")
    monkeypatch.setenv("ZENDESK_BASE_URL", "https://acme.zendesk.com")
    monkeypatch.setenv("ZENDESK_USER_EMAIL", "agent@example.com")
    monkeypatch.setenv("ZENDESK_API_TOKEN", "zd-token")
    monkeypatch.setenv("TRIAGE_ZENDESK_MAX_TICKETS", "3")
    limited = AppSettings()
    issue = FetchedIssue(
        issue_key="BC-9",
        summary="no extra zendesk ids in body",
        issue_type="Bug",
        reporter="support",
        zendesk_ticket_ids=["47322", "48661", "48950", "48951"],
    )
    fetcher = ZendeskTicketFetcher(limited)
    assert fetcher.collect_linked_ticket_ids(issue) == [
        "47322",
        "48661",
        "48950",
    ]


@pytest.mark.unit
def test_collect_linked_ticket_ids_with_stats_includes_cap_drops_for_custom_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JIRA_API_KEY", "jira-api-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-token")
    monkeypatch.setenv("TRIAGE_WEBHOOK_TOKEN", "triage-token")
    monkeypatch.setenv("TRIAGE_ZENDESK_CONTEXT_ENABLED", "true")
    monkeypatch.setenv("ZENDESK_BASE_URL", "https://acme.zendesk.com")
    monkeypatch.setenv("ZENDESK_USER_EMAIL", "agent@example.com")
    monkeypatch.setenv("ZENDESK_API_TOKEN", "zd-token")
    monkeypatch.setenv("TRIAGE_ZENDESK_MAX_TICKETS", "2")
    limited = AppSettings()
    issue = FetchedIssue(
        issue_key="BC-9",
        summary="extra ZD-999 in body",
        issue_type="Bug",
        reporter="support",
        zendesk_ticket_ids=["5001", "5002", "5003"],
    )
    fetcher = ZendeskTicketFetcher(limited)
    ticket_ids, deduped = fetcher.collect_linked_ticket_ids_with_stats(issue)
    assert ticket_ids == ["5001", "5002"]
    assert deduped == 2


@pytest.mark.unit
def test_collect_linked_ticket_ids_caps_body_text_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JIRA_API_KEY", "jira-api-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-token")
    monkeypatch.setenv("TRIAGE_WEBHOOK_TOKEN", "triage-token")
    monkeypatch.setenv("TRIAGE_ZENDESK_MAX_TICKETS", "2")
    limited = AppSettings()
    issue = FetchedIssue(
        issue_key="BC-9",
        summary="ZD-100 ZD-200 ZD-300",
        issue_type="Bug",
        reporter="support",
    )
    fetcher = ZendeskTicketFetcher(limited)
    assert fetcher.collect_linked_ticket_ids(issue) == ["100", "200"]


@pytest.mark.unit
def test_fetch_linked_tickets_enriches_issue_when_configured(settings: AppSettings) -> None:
    issue = FetchedIssue(
        issue_key="BC-1",
        summary="ignored when custom field ids present",
        issue_type="Bug",
        reporter="support",
        zendesk_ticket_ids=["777", "888"],
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("Authorization", "").startswith("Basic ")
        ticket_id = request.url.path.split("/")[-1].replace(".json", "")
        if request.url.path.endswith("/comments.json"):
            return httpx.Response(200, json={"comments": []})
        assert request.url.path in ("/api/v2/tickets/777.json", "/api/v2/tickets/888.json")
        return httpx.Response(
            200,
            json={
                "ticket": {
                    "id": int(ticket_id),
                    "subject": f"Subject {ticket_id}",
                    "description": f"Desc {ticket_id}",
                    "status": "open",
                    "priority": "high",
                },
            },
        )

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        fetcher = ZendeskTicketFetcher(settings, client=client)
        tickets = fetcher.fetch_linked_tickets(issue, run_id="run-zd")

    assert [t.ticket_id for t in tickets] == ["777", "888"]
    assert tickets[0].subject == "Subject 777"
    assert tickets[0].url == "https://acme.zendesk.com/agent/tickets/777"


@pytest.mark.unit
def test_fetch_ticket_parses_problem_id_from_api(settings: AppSettings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/comments.json"):
            return httpx.Response(200, json={"comments": []})
        assert request.url.path == "/api/v2/tickets/555.json"
        return httpx.Response(
            200,
            json={
                "ticket": {
                    "id": 555,
                    "subject": "Incident on problem 100",
                    "description": "Follow-up",
                    "status": "open",
                    "priority": "normal",
                    "problem_id": 100,
                },
            },
        )

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        fetcher = ZendeskTicketFetcher(settings, client=client)
        tickets = fetcher.fetch_tickets_by_ids(["555"])

    assert tickets[0].problem_id == "100"


@pytest.mark.unit
def test_fetch_linked_tickets_returns_empty_when_feature_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JIRA_API_KEY", "jira-api-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-token")
    monkeypatch.setenv("TRIAGE_WEBHOOK_TOKEN", "triage-token")
    monkeypatch.setenv("TRIAGE_ZENDESK_CONTEXT_ENABLED", "false")
    settings = AppSettings()
    issue = FetchedIssue(
        issue_key="BC-2",
        summary="ZD-101",
        issue_type="Bug",
        reporter="support",
        zendesk_ticket_ids=["101"],
    )
    fetcher = ZendeskTicketFetcher(settings)
    assert fetcher.fetch_linked_tickets(issue, run_id="run-zd-disabled") == []


def _ticket_and_comments_handler(
    *,
    ticket_id: str,
    comments: list[dict[str, object]] | None = None,
    comments_status: int = 200,
) -> httpx.MockTransport:
    """Mock Zendesk ticket + comments endpoints for one ticket id."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("Authorization", "").startswith("Basic ")
        if request.url.path == f"/api/v2/tickets/{ticket_id}.json":
            return httpx.Response(
                200,
                json={
                    "ticket": {
                        "id": int(ticket_id),
                        "subject": f"Subject {ticket_id}",
                        "description": f"Desc {ticket_id}",
                        "status": "open",
                        "priority": "high",
                    },
                },
            )
        if request.url.path == f"/api/v2/tickets/{ticket_id}/comments.json":
            if comments_status >= 400:
                return httpx.Response(comments_status, text="comments unavailable")
            return httpx.Response(200, json={"comments": comments or []})
        raise AssertionError(f"Unexpected Zendesk request: {request.url.path}")

    return httpx.MockTransport(handler)


@pytest.mark.unit
def test_fetch_ticket_includes_comments_newest_first(settings: AppSettings) -> None:
    transport = _ticket_and_comments_handler(
        ticket_id="777",
        comments=[
            {
                "id": 1,
                "body": "oldest public report",
                "public": True,
                "created_at": "2026-01-01T10:00:00Z",
            },
            {
                "id": 2,
                "body": "internal root-cause note",
                "public": False,
                "created_at": "2026-01-02T12:00:00Z",
            },
            {
                "id": 3,
                "body": "newest recovery update",
                "public": True,
                "created_at": "2026-01-03T15:00:00Z",
            },
        ],
    )
    with httpx.Client(transport=transport) as client:
        fetcher = ZendeskTicketFetcher(settings, client=client)
        tickets = fetcher.fetch_tickets_by_ids(["777"])

    assert len(tickets) == 1
    comments = tickets[0].comments
    assert [c.comment_id for c in comments] == ["3", "2", "1"]
    assert [c.body for c in comments] == [
        "newest recovery update",
        "internal root-cause note",
        "oldest public report",
    ]
    assert comments[1].public is False


@pytest.mark.unit
def test_fetch_ticket_comments_capped_by_max_comments_per_ticket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JIRA_API_KEY", "jira-api-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-token")
    monkeypatch.setenv("TRIAGE_WEBHOOK_TOKEN", "triage-token")
    monkeypatch.setenv("TRIAGE_ZENDESK_CONTEXT_ENABLED", "true")
    monkeypatch.setenv("ZENDESK_BASE_URL", "https://acme.zendesk.com")
    monkeypatch.setenv("ZENDESK_USER_EMAIL", "agent@example.com")
    monkeypatch.setenv("ZENDESK_API_TOKEN", "zd-token")
    monkeypatch.setenv("TRIAGE_ZENDESK_MAX_COMMENTS_PER_TICKET", "2")
    limited = AppSettings()

    transport = _ticket_and_comments_handler(
        ticket_id="888",
        comments=[
            {
                "id": 10,
                "body": "old",
                "public": True,
                "created_at": "2026-01-01T10:00:00Z",
            },
            {
                "id": 11,
                "body": "middle",
                "public": True,
                "created_at": "2026-01-02T10:00:00Z",
            },
            {
                "id": 12,
                "body": "newest",
                "public": True,
                "created_at": "2026-01-03T10:00:00Z",
            },
        ],
    )
    with httpx.Client(transport=transport) as client:
        fetcher = ZendeskTicketFetcher(limited, client=client)
        tickets = fetcher.fetch_tickets_by_ids(["888"])

    assert [c.body for c in tickets[0].comments] == ["newest", "middle"]


@pytest.mark.unit
def test_fetch_ticket_comment_fetch_soft_fails_leaves_empty_comments(
    settings: AppSettings,
) -> None:
    transport = _ticket_and_comments_handler(
        ticket_id="999",
        comments_status=503,
    )
    with httpx.Client(transport=transport) as client:
        fetcher = ZendeskTicketFetcher(settings, client=client)
        tickets = fetcher.fetch_tickets_by_ids(["999"])

    assert len(tickets) == 1
    assert tickets[0].ticket_id == "999"
    assert tickets[0].subject == "Subject 999"
    assert tickets[0].comments == []


@pytest.mark.unit
def test_fetch_tickets_by_ids_with_failures_continues_on_per_ticket_error(
    settings: AppSettings,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("Authorization", "").startswith("Basic ")
        if request.url.path == "/api/v2/tickets/100.json":
            return httpx.Response(
                200,
                json={
                    "ticket": {
                        "id": 100,
                        "subject": "Subject 100",
                        "description": "Desc 100",
                        "status": "open",
                        "priority": "high",
                    },
                },
            )
        if request.url.path == "/api/v2/tickets/100/comments.json":
            return httpx.Response(200, json={"comments": []})
        if request.url.path == "/api/v2/tickets/200.json":
            return httpx.Response(404, text="not found")
        raise AssertionError(f"Unexpected Zendesk request: {request.url.path}")

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        fetcher = ZendeskTicketFetcher(settings, client=client)
        result = fetcher.fetch_tickets_by_ids_with_failures(["100", "200"])

    assert len(result.tickets) == 1
    assert result.tickets[0].ticket_id == "100"
    assert len(result.failures) == 1
    assert result.failures[0].ticket_id == "200"
    assert result.failures[0].failure == "http_404"


@pytest.mark.unit
def test_extract_zendesk_inline_image_urls_from_markdown_and_html() -> None:
    text = (
        "See ![screenshot](https://acme.zendesk.com/attachments/token/abc123/?name=blobid0.png)\n"
        'Also <img src="https://cdn.zendesk.com/images/image-20260219-160437.png" alt="ui">'
    )
    urls = extract_zendesk_inline_image_urls(text)
    assert urls == [
        "https://acme.zendesk.com/attachments/token/abc123/?name=blobid0.png",
        "https://cdn.zendesk.com/images/image-20260219-160437.png",
    ]


@pytest.mark.unit
def test_fetch_ticket_comments_requests_newest_first_page_from_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: oldest-first default page must not hide newest comments on long tickets."""
    monkeypatch.setenv("JIRA_API_KEY", "jira-api-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-token")
    monkeypatch.setenv("TRIAGE_WEBHOOK_TOKEN", "triage-token")
    monkeypatch.setenv("TRIAGE_ZENDESK_CONTEXT_ENABLED", "true")
    monkeypatch.setenv("ZENDESK_BASE_URL", "https://acme.zendesk.com")
    monkeypatch.setenv("ZENDESK_USER_EMAIL", "agent@example.com")
    monkeypatch.setenv("ZENDESK_API_TOKEN", "zd-token")
    monkeypatch.setenv("TRIAGE_ZENDESK_MAX_COMMENTS_PER_TICKET", "3")
    limited = AppSettings()

    all_comments = [
        {
            "id": index,
            "body": f"comment-{index}",
            "public": True,
            "created_at": f"2026-01-{index:02d}T10:00:00Z",
        }
        for index in range(1, 11)
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/tickets/444.json":
            return httpx.Response(
                200,
                json={
                    "ticket": {
                        "id": 444,
                        "subject": "Long thread",
                        "description": "Desc",
                        "status": "open",
                        "priority": "normal",
                    },
                },
            )
        if request.url.path == "/api/v2/tickets/444/comments.json":
            sort_order = request.url.params.get("sort_order")
            per_page = int(request.url.params.get("per_page", "100"))
            ordered = sorted(
                all_comments,
                key=lambda item: str(item["created_at"]),
                reverse=sort_order == "desc",
            )
            return httpx.Response(200, json={"comments": ordered[:per_page]})
        raise AssertionError(f"Unexpected Zendesk request: {request.url.path}")

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        fetcher = ZendeskTicketFetcher(limited, client=client)
        tickets = fetcher.fetch_tickets_by_ids(["444"])

    assert [c.body for c in tickets[0].comments] == [
        "comment-10",
        "comment-9",
        "comment-8",
    ]


@pytest.mark.unit
def test_fetch_ticket_comments_requests_include_inline_images_and_newest_first(
    settings: AppSettings,
) -> None:
    captured_params: list[dict[str, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/tickets/555.json":
            return httpx.Response(
                200,
                json={
                    "ticket": {
                        "id": 555,
                        "subject": "Subject 555",
                        "description": "Desc 555",
                        "status": "open",
                        "priority": "normal",
                    },
                },
            )
        if request.url.path == "/api/v2/tickets/555/comments.json":
            captured_params.append(
                {
                    "include_inline_images": request.url.params.get("include_inline_images"),
                    "sort_order": request.url.params.get("sort_order"),
                    "per_page": request.url.params.get("per_page"),
                },
            )
            return httpx.Response(200, json={"comments": []})
        raise AssertionError(f"Unexpected Zendesk request: {request.url.path}")

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        fetcher = ZendeskTicketFetcher(settings, client=client)
        fetcher.fetch_tickets_by_ids(["555"])

    assert captured_params == [
        {
            "include_inline_images": "true",
            "sort_order": "desc",
            "per_page": str(settings.triage_zendesk_max_comments_per_ticket),
        },
    ]


@pytest.mark.unit
def test_fetch_ticket_parses_comment_attachment_and_inline_body_images(
    settings: AppSettings,
) -> None:
    inline_url = "https://acme.zendesk.com/attachments/token/inline/?name=blobid0.png"
    transport = _ticket_and_comments_handler(
        ticket_id="666",
        comments=[
            {
                "id": 42,
                "body": f"Customer screenshot: ![]({inline_url})",
                "public": True,
                "created_at": "2026-02-19T16:04:37Z",
                "attachments": [
                    {
                        "id": 9001,
                        "file_name": "image-20260219-160437.png",
                        "content_url": "https://acme.zendesk.com/attachments/token/file9001/",
                        "content_type": "image/png",
                        "size": 2048,
                    },
                    {
                        "id": 9002,
                        "file_name": "notes.txt",
                        "content_url": "https://acme.zendesk.com/attachments/token/file9002/",
                        "content_type": "text/plain",
                        "size": 128,
                    },
                ],
            },
        ],
    )
    with httpx.Client(transport=transport) as client:
        fetcher = ZendeskTicketFetcher(settings, client=client)
        tickets = fetcher.fetch_tickets_by_ids(["666"])

    assert len(tickets) == 1
    comments = tickets[0].comments
    assert len(comments) == 1
    refs = comments[0].image_refs
    assert refs == [
        ZendeskImageRef(
            url=inline_url,
            filename="blobid0.png",
            source="comment_inline_body",
        ),
        ZendeskImageRef(
            url="https://acme.zendesk.com/attachments/token/file9001/",
            filename="image-20260219-160437.png",
            attachment_id="9001",
            mime_type="image/png",
            size_bytes=2048,
            source="comment_attachment",
        ),
    ]


@pytest.mark.unit
def test_fetch_ticket_parses_description_inline_images(settings: AppSettings) -> None:
    description = (
        "Initial report with "
        "![](https://acme.zendesk.com/attachments/token/desc/?name=screenshot.png)"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/tickets/777.json":
            return httpx.Response(
                200,
                json={
                    "ticket": {
                        "id": 777,
                        "subject": "Subject 777",
                        "description": description,
                        "status": "open",
                        "priority": "high",
                    },
                },
            )
        if request.url.path == "/api/v2/tickets/777/comments.json":
            return httpx.Response(200, json={"comments": []})
        raise AssertionError(f"Unexpected Zendesk request: {request.url.path}")

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        fetcher = ZendeskTicketFetcher(settings, client=client)
        tickets = fetcher.fetch_tickets_by_ids(["777"])

    assert tickets[0].description_image_refs == [
        ZendeskImageRef(
            url="https://acme.zendesk.com/attachments/token/desc/?name=screenshot.png",
            filename="screenshot.png",
            source="ticket_description_inline",
        ),
    ]


@pytest.mark.unit
def test_fetch_image_bytes_returns_binary_with_basic_auth(settings: AppSettings) -> None:
    image_url = "https://acme.zendesk.com/attachments/token/abc123/?name=blobid0.png"
    image_ref = ZendeskImageRef(url=image_url, filename="blobid0.png", source="inline_body")
    png_bytes = b"\x89PNG\r\n\x1a\nfake-png"

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == image_url
        assert request.headers.get("Authorization", "").startswith("Basic ")
        assert request.headers.get("Accept") == "*/*"
        return httpx.Response(200, content=png_bytes)

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        fetcher = ZendeskTicketFetcher(settings, client=client)
        data = fetcher.fetch_image_bytes(image_ref, run_id="run-zd-img")

    assert data == png_bytes


@pytest.mark.unit
def test_fetch_image_bytes_follows_redirect_to_signed_cdn_url(settings: AppSettings) -> None:
    token_url = "https://acme.zendesk.com/attachments/token/redirect123/"
    cdn_url = "https://cdn.zendesk.com/images/blobid0.png"
    image_ref = ZendeskImageRef(url=token_url, filename="blobid0.png", source="inline_body")
    png_bytes = b"\x89PNG\r\n\x1a\nfrom-cdn"
    requests_seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_seen.append(str(request.url))
        if str(request.url) == token_url:
            assert request.headers.get("Authorization", "").startswith("Basic ")
            return httpx.Response(302, headers={"Location": cdn_url})
        if str(request.url) == cdn_url:
            return httpx.Response(200, content=png_bytes)
        raise AssertionError(f"Unexpected image fetch URL: {request.url}")

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport, follow_redirects=True) as client:
        fetcher = ZendeskTicketFetcher(settings, client=client)
        data = fetcher.fetch_image_bytes(image_ref, run_id="run-zd-redirect")

    assert data == png_bytes
    assert requests_seen == [token_url, cdn_url]


@pytest.mark.unit
def test_fetch_image_bytes_raises_on_http_error(settings: AppSettings) -> None:
    image_ref = ZendeskImageRef(
        url="https://acme.zendesk.com/attachments/token/missing/",
        filename="gone.png",
        source="attachment",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        fetcher = ZendeskTicketFetcher(settings, client=client)
        with pytest.raises(ZendeskTicketFetchError, match="HTTP 404"):
            fetcher.fetch_image_bytes(image_ref, run_id="run-zd-missing")


@pytest.mark.unit
def test_fetch_image_bytes_raises_when_credentials_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JIRA_API_KEY", "jira-api-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-token")
    monkeypatch.setenv("TRIAGE_WEBHOOK_TOKEN", "triage-token")
    settings = AppSettings()
    fetcher = ZendeskTicketFetcher(settings)
    image_ref = ZendeskImageRef(
        url="https://acme.zendesk.com/attachments/token/abc/",
        filename="blobid0.png",
        source="inline_body",
    )
    with pytest.raises(ZendeskTicketFetchError, match="credentials required"):
        fetcher.fetch_image_bytes(image_ref, run_id="run-zd-no-creds")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://acme.zendesk.com/attachments/token/abc/", True),
        ("https://cdn.zendesk.com/images/screenshot.png", True),
        ("https://static.zdassets.com/hc/assets/photo.png", True),
        ("https://evil.example.com/steal-token.png", False),
        ("https://attacker.io/fake-zendesk.png", False),
    ],
)
def test_is_trusted_zendesk_image_url_recognizes_configured_and_cdn_hosts(
    url: str,
    expected: bool,
) -> None:
    assert (
        is_trusted_zendesk_image_url(
            url,
            zendesk_base_url="https://acme.zendesk.com",
        )
        is expected
    )


@pytest.mark.unit
def test_fetch_image_bytes_omits_auth_for_external_image_url(settings: AppSettings) -> None:
    external_url = "https://evil.example.com/inline-screenshot.png"
    image_ref = ZendeskImageRef(
        url=external_url,
        filename="inline-screenshot.png",
        source="inline_body",
    )
    png_bytes = b"\x89PNG\r\n\x1a\npublic-image"

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == external_url
        assert "Authorization" not in request.headers
        assert request.headers.get("Accept") == "*/*"
        return httpx.Response(200, content=png_bytes)

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        fetcher = ZendeskTicketFetcher(settings, client=client)
        data = fetcher.fetch_image_bytes(image_ref, run_id="run-zd-external")

    assert data == png_bytes


@pytest.mark.unit
def test_fetch_image_bytes_external_url_does_not_require_zendesk_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JIRA_API_KEY", "jira-api-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-token")
    monkeypatch.setenv("TRIAGE_WEBHOOK_TOKEN", "triage-token")
    settings = AppSettings()
    fetcher = ZendeskTicketFetcher(settings)
    image_ref = ZendeskImageRef(
        url="https://public.example.com/screenshot.png",
        filename="screenshot.png",
        source="inline_body",
    )
    png_bytes = b"\x89PNG\r\n\x1a\npublic"

    def handler(request: httpx.Request) -> httpx.Response:
        assert "Authorization" not in request.headers
        return httpx.Response(200, content=png_bytes)

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        fetcher = ZendeskTicketFetcher(settings, client=client)
        data = fetcher.fetch_image_bytes(image_ref, run_id="run-zd-external-no-creds")

    assert data == png_bytes
