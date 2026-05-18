"""Unit tests for Jira REST issue fetch by key."""

from __future__ import annotations

import httpx
import pytest

from triage_service.adapters.jira_issue_fetcher import (
    FetchedIssue,
    JiraIssueFetchError,
    JiraIssueFetcher,
)
from triage_service.core.settings import AppSettings


@pytest.fixture
def jira_app_settings(monkeypatch: pytest.MonkeyPatch) -> AppSettings:
    monkeypatch.setenv("JIRA_API_KEY", "jira-api-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-token")
    monkeypatch.setenv("TRIAGE_WEBHOOK_TOKEN", "triage-token")
    monkeypatch.setenv("JIRA_CLOUD_ID", "cloud-id-test")
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
        assert request.url.path == "/ex/jira/cloud-id-test/rest/api/3/issue/TJC-42"
        assert "fields=summary" in str(request.url)
        assert "customfield_10251" in str(request.url)
        auth = request.headers.get("Authorization", "")
        assert auth.startswith("Basic ")
        return httpx.Response(200, json=body)

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        fetcher = JiraIssueFetcher(jira_app_settings, client=client)
        issue = fetcher.fetch("TJC-42", run_id="run-test")

    assert issue == FetchedIssue(
        issue_key="TJC-42",
        summary="Cannot log in",
        description="Steps to reproduce",
        reproduction_steps="Steps to reproduce",
        issue_type="Bug",
        priority="High",
        reporter="Alice Support",
        reporter_account_id="61d4a5c6e67ea2006bce3aaa",
    )


@pytest.mark.unit
def test_fetch_issue_extracts_reproduction_steps_section_from_description(
    jira_app_settings: AppSettings,
) -> None:
    body = {
        "key": "TJC-77",
        "fields": {
            "summary": "API call fails",
            "description": (
                "Environment: staging\n"
                "Steps to reproduce:\n"
                "1. Open client\n"
                "2. Send payload\n"
                "3. Observe 500\n"
                "Expected: 200"
            ),
            "issuetype": {"name": "Bug"},
            "priority": {"name": "High"},
            "reporter": {"displayName": "Alice Support"},
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        fetcher = JiraIssueFetcher(jira_app_settings, client=client)
        issue = fetcher.fetch("TJC-77", run_id="run-test")

    assert issue.reproduction_steps is not None
    assert "open client" in issue.reproduction_steps.lower()


@pytest.mark.unit
def test_fetch_issue_prefers_configured_reproduction_steps_custom_field(
    jira_app_settings: AppSettings,
) -> None:
    body = {
        "key": "TJC-88",
        "fields": {
            "summary": "Upload fails",
            "description": "No repro marker in this description",
            "customfield_10251": {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [{"type": "text", "text": "See video above"}],
                    },
                ],
            },
            "issuetype": {"name": "Bug"},
            "priority": {"name": "High"},
            "reporter": {"displayName": "Alice Support"},
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert "customfield_10251" in str(request.url)
        return httpx.Response(200, json=body)

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        fetcher = JiraIssueFetcher(jira_app_settings, client=client)
        issue = fetcher.fetch("TJC-88", run_id="run-test")

    assert issue.reproduction_steps == "See video above"


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
        issue = fetcher.fetch("BC-1", run_id="run-test")

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
    monkeypatch.setenv("TRIAGE_WEBHOOK_TOKEN", "triage-token")
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
        issue = fetcher.fetch("TJC-42", run_id="run-test")
    assert issue.issue_key == "TJC-42"


@pytest.mark.unit
def test_fetch_issue_uses_jira_cloud_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JIRA_API_KEY", "jira-api-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-token")
    monkeypatch.setenv("TRIAGE_WEBHOOK_TOKEN", "triage-token")
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
        fetcher.fetch("X-1", run_id="run-test")


@pytest.mark.unit
def test_fetch_issue_raises_on_http_error(jira_app_settings: AppSettings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="Issue does not exist")

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        fetcher = JiraIssueFetcher(jira_app_settings, client=client)
        with pytest.raises(JiraIssueFetchError) as exc:
            fetcher.fetch("TJC-999", run_id="run-test")
    assert "404" in str(exc.value)


@pytest.mark.unit
def test_fetch_issue_raises_when_jira_cloud_id_and_base_url_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JIRA_API_KEY", "jira-api-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-token")
    monkeypatch.setenv("TRIAGE_WEBHOOK_TOKEN", "triage-token")
    settings = AppSettings()

    transport = httpx.MockTransport(lambda r: httpx.Response(200, json={}))
    with httpx.Client(transport=transport) as client:
        fetcher = JiraIssueFetcher(settings, client=client)
        with pytest.raises(JiraIssueFetchError) as exc:
            fetcher.fetch("TJC-1", run_id="run-test")
    msg = str(exc.value).lower()
    assert "jira_cloud_id" in msg or "cloud_id" in msg


@pytest.mark.unit
def test_fetch_issue_raises_when_jira_user_email_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JIRA_API_KEY", "jira-api-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-token")
    monkeypatch.setenv("TRIAGE_WEBHOOK_TOKEN", "triage-token")
    monkeypatch.setenv("JIRA_CLOUD_ID", "cloud-id-test")
    settings = AppSettings()

    transport = httpx.MockTransport(lambda r: httpx.Response(200, json={}))
    with httpx.Client(transport=transport) as client:
        fetcher = JiraIssueFetcher(settings, client=client)
        with pytest.raises(JiraIssueFetchError) as exc:
            fetcher.fetch("TJC-1", run_id="run-test")
    assert "email" in str(exc.value).lower()


@pytest.mark.unit
def test_fetch_issue_retries_on_transient_503_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
    jira_app_settings: AppSettings,
) -> None:
    monkeypatch.setattr(
        "triage_service.adapters.jira_http_retry.time.sleep",
        lambda _s: None,
    )
    attempts: list[int] = []
    body = {
        "key": "TJC-42",
        "fields": {
            "summary": "ok",
            "description": None,
            "issuetype": {"name": "Bug"},
            "priority": None,
            "reporter": {"displayName": "r"},
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(503, text="unavailable")
        return httpx.Response(200, json=body)

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        fetcher = JiraIssueFetcher(jira_app_settings, client=client)
        issue = fetcher.fetch("TJC-42", run_id="run-test")
    assert len(attempts) == 2
    assert issue.summary == "ok"


@pytest.mark.unit
def test_fetch_issue_raises_after_exhausting_retries_on_persistent_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JIRA_API_KEY", "jira-api-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-token")
    monkeypatch.setenv("TRIAGE_WEBHOOK_TOKEN", "triage-token")
    monkeypatch.setenv("JIRA_CLOUD_ID", "cloud-id-test")
    monkeypatch.setenv("JIRA_USER_EMAIL", "bot@example.com")
    monkeypatch.setenv("TRIAGE_JIRA_HTTP_MAX_RETRIES", "1")
    monkeypatch.setattr(
        "triage_service.adapters.jira_http_retry.time.sleep",
        lambda _s: None,
    )
    settings = AppSettings()
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(503, text="still down")

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        fetcher = JiraIssueFetcher(settings, client=client)
        with pytest.raises(JiraIssueFetchError) as exc:
            fetcher.fetch("TJC-1", run_id="run-test")
    assert len(attempts) == 2
    assert "503" in str(exc.value)
    assert exc.value.attempts == 2
    assert exc.value.http_status == 503


@pytest.mark.unit
def test_fetch_issue_read_timeout_exhausted_sets_transport_timeout_and_kind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JIRA_API_KEY", "jira-api-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-token")
    monkeypatch.setenv("TRIAGE_WEBHOOK_TOKEN", "triage-token")
    monkeypatch.setenv("JIRA_CLOUD_ID", "cloud-id-test")
    monkeypatch.setenv("JIRA_USER_EMAIL", "bot@example.com")
    monkeypatch.setattr(
        "triage_service.adapters.jira_http_retry.time.sleep",
        lambda *_a, **_k: None,
    )
    settings = AppSettings()
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ReadTimeout("read stalled", request=request)

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        fetcher = JiraIssueFetcher(settings, client=client)
        with pytest.raises(JiraIssueFetchError) as exc:
            fetcher.fetch("TJC-1", run_id="run-test")
    assert calls["n"] == 3
    assert exc.value.attempts == 3
    assert exc.value.transport_timeout is True
    assert exc.value.transport_error_kind == "timeout"
    assert exc.value.http_status is None


@pytest.mark.unit
def test_fetch_issue_transport_exhausted_records_attempts_and_error_kind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JIRA_API_KEY", "jira-api-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-token")
    monkeypatch.setenv("TRIAGE_WEBHOOK_TOKEN", "triage-token")
    monkeypatch.setenv("JIRA_CLOUD_ID", "cloud-id-test")
    monkeypatch.setenv("JIRA_USER_EMAIL", "bot@example.com")
    monkeypatch.setenv("TRIAGE_JIRA_HTTP_MAX_RETRIES", "2")
    monkeypatch.setattr(
        "triage_service.adapters.jira_http_retry.time.sleep",
        lambda *_a, **_k: None,
    )
    settings = AppSettings()
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        _ = request
        calls["n"] += 1
        raise httpx.ConnectError("refused", request=httpx.Request("GET", "https://x"))

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        fetcher = JiraIssueFetcher(settings, client=client)
        with pytest.raises(JiraIssueFetchError) as exc:
            fetcher.fetch("TJC-1", run_id="run-test")
    assert calls["n"] == 3
    assert exc.value.attempts == 3
    assert exc.value.http_status is None
    assert exc.value.transport_timeout is False
    assert exc.value.transport_error_kind == "connect_error"
