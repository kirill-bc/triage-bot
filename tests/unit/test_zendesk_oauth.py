"""Zendesk OAuth client-credentials settings, token minting, and fetcher wiring."""

from __future__ import annotations

import json
import time
from dataclasses import replace

import httpx
import pytest

from triage_service.adapters.zendesk_oauth import (
    ZendeskOAuthClient,
    ZendeskOAuthError,
)
from triage_service.adapters.zendesk_ticket_fetcher import (
    ZendeskTicketFetcher,
)
from triage_service.core.settings import AppSettings


def _oauth_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JIRA_API_KEY", "jira")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or")
    monkeypatch.setenv("TRIAGE_WEBHOOK_TOKEN", "triage")
    monkeypatch.setenv("TRIAGE_ZENDESK_ENABLE_OAUTH", "true")
    monkeypatch.setenv("ZENDESK_BASE_URL", "https://acme.zendesk.com")
    monkeypatch.setenv("ZENDESK_IDENTIFIER", "jira_bug_triage_bot")
    monkeypatch.setenv("ZENDESK_SECRET", "oauth-secret")


@pytest.mark.unit
def test_zendesk_oauth_configured_requires_identifier_and_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JIRA_API_KEY", "jira")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or")
    monkeypatch.setenv("TRIAGE_WEBHOOK_TOKEN", "triage")
    monkeypatch.setenv("TRIAGE_ZENDESK_ENABLE_OAUTH", "true")
    monkeypatch.setenv("ZENDESK_BASE_URL", "https://acme.zendesk.com")
    monkeypatch.setenv("ZENDESK_IDENTIFIER", "jira_bug_triage_bot")
    monkeypatch.delenv("ZENDESK_SECRET", raising=False)
    settings = AppSettings()
    assert settings.zendesk_oauth_configured is False


@pytest.mark.unit
def test_resolve_zendesk_subdomain_from_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JIRA_API_KEY", "jira")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or")
    monkeypatch.setenv("TRIAGE_WEBHOOK_TOKEN", "triage")
    monkeypatch.setenv("ZENDESK_BASE_URL", "https://britecore.zendesk.com")
    settings = AppSettings()
    assert settings.resolve_zendesk_subdomain() == "britecore"


@pytest.mark.unit
def test_oauth_client_mints_token_with_client_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _oauth_env(monkeypatch)
    settings = AppSettings()
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "access_token": "new-access",
                "expires_in": 3600,
                "token_type": "bearer",
            },
        )

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        oauth = ZendeskOAuthClient(settings, client=client)
        token = oauth.get_access_token()

    assert token == "new-access"
    assert captured["path"] == "/oauth/tokens"
    assert captured["body"] == {
        "grant_type": "client_credentials",
        "client_id": "jira_bug_triage_bot",
        "client_secret": "oauth-secret",
        "scope": "read",
    }


@pytest.mark.unit
def test_oauth_client_reuses_cached_access_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _oauth_env(monkeypatch)
    settings = AppSettings()
    token_calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal token_calls
        token_calls += 1
        return httpx.Response(
            200,
            json={
                "access_token": "cached-access",
                "expires_in": 3600,
                "token_type": "bearer",
            },
        )

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        oauth = ZendeskOAuthClient(settings, client=client)
        first = oauth.get_access_token()
        second = oauth.get_access_token()

    assert first == "cached-access"
    assert second == "cached-access"
    assert token_calls == 1


@pytest.mark.unit
def test_oauth_client_remints_expired_access_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _oauth_env(monkeypatch)
    settings = AppSettings()
    token_calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal token_calls
        token_calls += 1
        suffix = str(token_calls)
        return httpx.Response(
            200,
            json={
                "access_token": f"access-{suffix}",
                "expires_in": 30 if token_calls == 1 else 3600,
                "token_type": "bearer",
            },
        )

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        oauth = ZendeskOAuthClient(settings, client=client)
        first = oauth.get_access_token()
        # Force expiry past refresh buffer (60s).
        assert oauth._cached_token is not None
        oauth._cached_token = replace(  # noqa: SLF001
            oauth._cached_token,
            expires_at=time.time() - 120.0,
        )
        second = oauth.get_access_token()

    assert first == "access-1"
    assert second == "access-2"
    assert token_calls == 2


@pytest.mark.unit
def test_oauth_client_raises_when_token_response_missing_access_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _oauth_env(monkeypatch)
    settings = AppSettings()

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"token_type": "bearer"})

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        oauth = ZendeskOAuthClient(settings, client=client)
        with pytest.raises(ZendeskOAuthError, match="missing access token"):
            oauth.get_access_token()


@pytest.mark.unit
def test_fetcher_uses_bearer_auth_when_oauth_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _oauth_env(monkeypatch)
    monkeypatch.setenv("TRIAGE_ZENDESK_CONTEXT_ENABLED", "true")
    settings = AppSettings()
    auth_headers: list[str] = []
    token_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_calls
        if request.url.path == "/oauth/tokens":
            token_calls += 1
            return httpx.Response(
                200,
                json={
                    "access_token": "oauth-access-token",
                    "expires_in": 3600,
                    "token_type": "bearer",
                },
            )
        auth_headers.append(request.headers.get("Authorization", ""))
        if request.url.path == "/api/v2/tickets/42.json":
            return httpx.Response(
                200,
                json={
                    "ticket": {
                        "id": 42,
                        "subject": "Subject",
                        "description": "Body",
                        "status": "open",
                        "priority": "normal",
                    },
                },
            )
        if request.url.path == "/api/v2/tickets/42/comments.json":
            return httpx.Response(200, json={"comments": []})
        raise AssertionError(f"Unexpected request: {request.url.path}")

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        oauth = ZendeskOAuthClient(settings, client=client)
        fetcher = ZendeskTicketFetcher(settings, client=client, oauth_client=oauth)
        tickets = fetcher.fetch_tickets_by_ids(["42"])

    assert len(tickets) == 1
    assert token_calls == 1
    assert auth_headers
    assert auth_headers[0] == "Bearer oauth-access-token"
