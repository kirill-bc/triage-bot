"""Unit tests for Jira REST issue fetch by key."""

from __future__ import annotations

import httpx
import pytest

from jira_issue_fetcher import FetchedIssue, JiraIssueFetchError, JiraIssueFetcher
from settings import AppSettings


@pytest.fixture
def jira_app_settings(monkeypatch: pytest.MonkeyPatch) -> AppSettings:
    monkeypatch.setenv("JIRA_API_KEY", "jira-api-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-token")
    monkeypatch.setenv("JIRA_BASE_URL", "https://example.atlassian.net")
    monkeypatch.setenv("JIRA_USER_EMAIL", "bot@example.com")
    return AppSettings()


@pytest.mark.unit
def test_fetch_issue_returns_summary_description_type_priority_reporter(
    jira_app_settings: AppSettings,
) -> None:
    body = {
        "key": "TJC-42",
        "fields": {
            "summary": "Cannot log in",
            "description": {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [{"type": "text", "text": "Steps to reproduce"}],
                    },
                ],
            },
            "issuetype": {"name": "Bug"},
            "priority": {"name": "High"},
            "reporter": {
                "displayName": "Alice Support",
                "emailAddress": "alice@example.com",
                "accountId": "61d4a5c6e67ea2006bce3aaa",
            },
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/rest/api/3/issue/TJC-42"
        assert "fields=summary" in str(request.url)
        auth = request.headers.get("Authorization", "")
        assert auth.startswith("Basic ")
        return httpx.Response(200, json=body)

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        fetcher = JiraIssueFetcher(jira_app_settings, client=client)
        issue = fetcher.fetch("TJC-42")

    assert issue == FetchedIssue(
        issue_key="TJC-42",
        summary="Cannot log in",
        description="Steps to reproduce",
        issue_type="Bug",
        priority="High",
        reporter="Alice Support",
        reporter_account_id="61d4a5c6e67ea2006bce3aaa",
    )


@pytest.mark.unit
def test_fetch_issue_uses_reporter_account_id_when_display_name_missing(
    jira_app_settings: AppSettings,
) -> None:
    body = {
        "key": "BC-1",
        "fields": {
            "summary": "x",
            "description": None,
            "issuetype": {"name": "Story"},
            "priority": None,
            "reporter": {"accountId": "acc-123"},
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        fetcher = JiraIssueFetcher(jira_app_settings, client=client)
        issue = fetcher.fetch("BC-1")

    assert issue.description is None
    assert issue.priority is None
    assert issue.reporter == "acc-123"
    assert issue.reporter_account_id == "acc-123"


@pytest.mark.unit
def test_fetch_issue_uses_atlassian_gateway_when_jira_cloud_id_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JIRA_API_KEY", "jira-api-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-token")
    monkeypatch.setenv("JIRA_CLOUD_ID", "550e8400-e29b-41d4-a716-446655440000")
    monkeypatch.setenv("JIRA_USER_EMAIL", "bot@example.com")
    settings = AppSettings()

    body = {
        "key": "TJC-42",
        "fields": {
            "summary": "x",
            "description": None,
            "issuetype": {"name": "Bug"},
            "priority": None,
            "reporter": {"displayName": "r"},
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.host == "api.atlassian.com"
        assert (
            request.url.path
            == "/ex/jira/550e8400-e29b-41d4-a716-446655440000/rest/api/3/issue/TJC-42"
        )
        assert "fields=summary" in str(request.url)
        return httpx.Response(200, json=body)

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        fetcher = JiraIssueFetcher(settings, client=client)
        issue = fetcher.fetch("TJC-42")
    assert issue.issue_key == "TJC-42"


@pytest.mark.unit
def test_fetch_issue_prefers_jira_cloud_id_over_jira_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gateway URL wins when both are configured (matches service-account curl style)."""
    monkeypatch.setenv("JIRA_API_KEY", "jira-api-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-token")
    monkeypatch.setenv("JIRA_BASE_URL", "https://legacy.example.atlassian.net")
    monkeypatch.setenv("JIRA_CLOUD_ID", "cloud-id-1")
    monkeypatch.setenv("JIRA_USER_EMAIL", "bot@example.com")
    settings = AppSettings()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.atlassian.com"
        assert "/ex/jira/cloud-id-1/" in request.url.path
        return httpx.Response(
            200,
            json={
                "key": "X-1",
                "fields": {
                    "summary": "s",
                    "description": None,
                    "issuetype": {"name": "Story"},
                    "priority": None,
                    "reporter": {"displayName": "r"},
                },
            },
        )

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        fetcher = JiraIssueFetcher(settings, client=client)
        fetcher.fetch("X-1")


@pytest.mark.unit
def test_fetch_issue_raises_on_http_error(jira_app_settings: AppSettings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="Issue does not exist")

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        fetcher = JiraIssueFetcher(jira_app_settings, client=client)
        with pytest.raises(JiraIssueFetchError) as exc:
            fetcher.fetch("TJC-999")
    assert "404" in str(exc.value)


@pytest.mark.unit
def test_fetch_issue_raises_when_jira_cloud_id_and_base_url_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JIRA_API_KEY", "jira-api-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-token")
    settings = AppSettings()

    transport = httpx.MockTransport(lambda r: httpx.Response(200, json={}))
    with httpx.Client(transport=transport) as client:
        fetcher = JiraIssueFetcher(settings, client=client)
        with pytest.raises(JiraIssueFetchError) as exc:
            fetcher.fetch("TJC-1")
    msg = str(exc.value).lower()
    assert "jira_cloud_id" in msg or "cloud_id" in msg
    assert "jira_base_url" in msg or "base_url" in msg


@pytest.mark.unit
def test_fetch_issue_raises_when_jira_user_email_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JIRA_API_KEY", "jira-api-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-token")
    monkeypatch.setenv("JIRA_BASE_URL", "https://example.atlassian.net")
    settings = AppSettings()

    transport = httpx.MockTransport(lambda r: httpx.Response(200, json={}))
    with httpx.Client(transport=transport) as client:
        fetcher = JiraIssueFetcher(settings, client=client)
        with pytest.raises(JiraIssueFetchError) as exc:
            fetcher.fetch("TJC-1")
    assert "email" in str(exc.value).lower()
