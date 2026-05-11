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
            "reporter": {"displayName": "Alice Support", "emailAddress": "alice@example.com"},
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
def test_fetch_issue_raises_when_jira_base_url_missing(
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
    assert "base url" in str(exc.value).lower()


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
