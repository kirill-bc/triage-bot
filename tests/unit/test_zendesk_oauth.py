"""Zendesk OAuth settings, token store, and API wiring."""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from triage_service.adapters.zendesk_oauth import (
    ZendeskOAuthClient,
    ZendeskOAuthTokenStore,
    create_oauth_state,
    verify_oauth_state,
)
from triage_service.adapters.zendesk_ticket_fetcher import (
    ZendeskTicketFetcher,
)
from triage_service.api.triage_api import create_app
from triage_service.core.settings import AppSettings

_TRIAGE_TOKEN = "test-triage-token"


@pytest.fixture(autouse=True)
def _configure_triage_webhook_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRIAGE_WEBHOOK_TOKEN", _TRIAGE_TOKEN)


def _auth_headers(*, token: str = _TRIAGE_TOKEN) -> dict[str, str]:
    return {"X-Triage-Token": token}


@pytest.mark.unit
def test_resolve_zendesk_oauth_redirect_uri_prefers_explicit_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JIRA_API_KEY", "jira")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or")
    monkeypatch.setenv("TRIAGE_WEBHOOK_TOKEN", "triage")
    monkeypatch.setenv("TRIAGE_ZENDESK_ENABLE_OAUTH", "true")
    monkeypatch.setenv("ZENDESK_REDIRECT_URI", "https://triage.example.com/zendesk/oauth/callback")
    monkeypatch.setenv("TRIAGE_PUBLIC_BASE_URL", "https://ignored.example.com")
    settings = AppSettings()
    assert settings.resolve_zendesk_oauth_redirect_uri() == (
        "https://triage.example.com/zendesk/oauth/callback"
    )


@pytest.mark.unit
def test_resolve_zendesk_oauth_redirect_uri_builds_from_public_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JIRA_API_KEY", "jira")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or")
    monkeypatch.setenv("TRIAGE_WEBHOOK_TOKEN", "triage")
    monkeypatch.setenv("TRIAGE_ZENDESK_ENABLE_OAUTH", "true")
    monkeypatch.setenv("TRIAGE_PUBLIC_BASE_URL", "https://triage.example.com/")
    settings = AppSettings()
    assert settings.resolve_zendesk_oauth_redirect_uri() == (
        "https://triage.example.com/zendesk/oauth/callback"
    )


@pytest.mark.unit
def test_resolve_zendesk_subdomain_from_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JIRA_API_KEY", "jira")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or")
    monkeypatch.setenv("TRIAGE_WEBHOOK_TOKEN", "triage")
    monkeypatch.setenv("ZENDESK_BASE_URL", "https://britecore.zendesk.com")
    settings = AppSettings()
    assert settings.resolve_zendesk_subdomain() == "britecore"


@pytest.mark.unit
def test_oauth_state_round_trip() -> None:
    state = create_oauth_state(secret="client-secret")
    assert verify_oauth_state(state, secret="client-secret") is True
    assert verify_oauth_state(state, secret="wrong-secret") is False


@pytest.mark.unit
def test_token_store_persists_and_loads_tokens(tmp_path: Path) -> None:
    store = ZendeskOAuthTokenStore(tmp_path / "tokens.json")
    store.save(
        access_token="access-1",
        refresh_token="refresh-1",
        expires_at=time.time() + 3600.0,
    )
    loaded = store.load()
    assert loaded is not None
    assert loaded.access_token == "access-1"
    assert loaded.refresh_token == "refresh-1"


@pytest.mark.unit
def test_oauth_client_exchanges_authorization_code(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("JIRA_API_KEY", "jira")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or")
    monkeypatch.setenv("TRIAGE_WEBHOOK_TOKEN", "triage")
    monkeypatch.setenv("TRIAGE_ZENDESK_ENABLE_OAUTH", "true")
    monkeypatch.setenv("ZENDESK_BASE_URL", "https://acme.zendesk.com")
    monkeypatch.setenv("ZENDESK_IDENTIFIER", "jira_bug_triage_bot")
    monkeypatch.setenv("ZENDESK_SECRET", "oauth-secret")
    monkeypatch.setenv(
        "ZENDESK_REDIRECT_URI",
        "https://triage.example.com/zendesk/oauth/callback",
    )
    monkeypatch.setenv("ZENDESK_OAUTH_TOKEN_FILE", str(tmp_path / "tokens.json"))
    settings = AppSettings()
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "access_token": "new-access",
                "refresh_token": "new-refresh",
                "expires_in": 3600,
                "token_type": "bearer",
            },
        )

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        oauth = ZendeskOAuthClient(settings, client=client)
        oauth.exchange_authorization_code("auth-code-123")

    assert captured["path"] == "/oauth/tokens"
    assert captured["body"] == {
        "grant_type": "authorization_code",
        "code": "auth-code-123",
        "client_id": "jira_bug_triage_bot",
        "client_secret": "oauth-secret",
        "redirect_uri": "https://triage.example.com/zendesk/oauth/callback",
        "scope": "read",
    }
    stored = ZendeskOAuthTokenStore(tmp_path / "tokens.json").load()
    assert stored is not None
    assert stored.access_token == "new-access"
    assert stored.refresh_token == "new-refresh"


@pytest.mark.unit
def test_oauth_client_refreshes_expired_access_token(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("JIRA_API_KEY", "jira")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or")
    monkeypatch.setenv("TRIAGE_WEBHOOK_TOKEN", "triage")
    monkeypatch.setenv("TRIAGE_ZENDESK_ENABLE_OAUTH", "true")
    monkeypatch.setenv("ZENDESK_BASE_URL", "https://acme.zendesk.com")
    monkeypatch.setenv("ZENDESK_IDENTIFIER", "jira_bug_triage_bot")
    monkeypatch.setenv("ZENDESK_SECRET", "oauth-secret")
    monkeypatch.setenv(
        "ZENDESK_REDIRECT_URI",
        "https://triage.example.com/zendesk/oauth/callback",
    )
    token_path = tmp_path / "tokens.json"
    monkeypatch.setenv("ZENDESK_OAUTH_TOKEN_FILE", str(token_path))
    ZendeskOAuthTokenStore(token_path).save(
        access_token="expired-access",
        refresh_token="refresh-keep",
        expires_at=time.time() - 60.0,
    )
    settings = AppSettings()
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "access_token": "fresh-access",
                "refresh_token": "refresh-keep",
                "expires_in": 7200,
                "token_type": "bearer",
            },
        )

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        oauth = ZendeskOAuthClient(settings, client=client)
        token = oauth.get_access_token()

    assert token == "fresh-access"
    assert captured["body"] == {
        "grant_type": "refresh_token",
        "refresh_token": "refresh-keep",
        "client_id": "jira_bug_triage_bot",
        "client_secret": "oauth-secret",
        "scope": "read",
    }


@pytest.mark.unit
def test_fetcher_uses_bearer_auth_when_oauth_enabled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("JIRA_API_KEY", "jira")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or")
    monkeypatch.setenv("TRIAGE_WEBHOOK_TOKEN", "triage")
    monkeypatch.setenv("TRIAGE_ZENDESK_CONTEXT_ENABLED", "true")
    monkeypatch.setenv("TRIAGE_ZENDESK_ENABLE_OAUTH", "true")
    monkeypatch.setenv("ZENDESK_BASE_URL", "https://acme.zendesk.com")
    monkeypatch.setenv("ZENDESK_IDENTIFIER", "jira_bug_triage_bot")
    monkeypatch.setenv("ZENDESK_SECRET", "oauth-secret")
    monkeypatch.setenv(
        "ZENDESK_REDIRECT_URI",
        "https://triage.example.com/zendesk/oauth/callback",
    )
    token_path = tmp_path / "tokens.json"
    monkeypatch.setenv("ZENDESK_OAUTH_TOKEN_FILE", str(token_path))
    ZendeskOAuthTokenStore(token_path).save(
        access_token="oauth-access-token",
        refresh_token="oauth-refresh-token",
        expires_at=time.time() + 3600.0,
    )
    settings = AppSettings()
    auth_headers: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
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
        fetcher = ZendeskTicketFetcher(settings, client=client)
        tickets = fetcher.fetch_tickets_by_ids(["42"])

    assert len(tickets) == 1
    assert auth_headers
    assert auth_headers[0] == "Bearer oauth-access-token"


@pytest.mark.unit
def test_get_redirect_uri_endpoint_returns_configured_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JIRA_API_KEY", "jira")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or")
    monkeypatch.setenv("TRIAGE_ZENDESK_ENABLE_OAUTH", "true")
    monkeypatch.setenv(
        "ZENDESK_REDIRECT_URI",
        "https://triage.example.com/zendesk/oauth/callback",
    )
    client = TestClient(create_app())
    response = client.get("/zendesk/oauth/redirect-uri")
    assert response.status_code == 200
    assert response.json() == {
        "redirect_uri": "https://triage.example.com/zendesk/oauth/callback",
        "authorize_path": "/zendesk/oauth/authorize",
        "callback_path": "/zendesk/oauth/callback",
    }


@pytest.mark.unit
def test_authorize_endpoint_redirects_to_zendesk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JIRA_API_KEY", "jira")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or")
    monkeypatch.setenv("TRIAGE_ZENDESK_ENABLE_OAUTH", "true")
    monkeypatch.setenv("ZENDESK_BASE_URL", "https://acme.zendesk.com")
    monkeypatch.setenv("ZENDESK_IDENTIFIER", "jira_bug_triage_bot")
    monkeypatch.setenv("ZENDESK_SECRET", "oauth-secret")
    monkeypatch.setenv(
        "ZENDESK_REDIRECT_URI",
        "https://triage.example.com/zendesk/oauth/callback",
    )
    client = TestClient(create_app(), follow_redirects=False)
    response = client.get("/zendesk/oauth/authorize")
    assert response.status_code == 307
    location = response.headers["location"]
    assert location.startswith("https://acme.zendesk.com/oauth/authorizations/new?")
    assert "client_id=jira_bug_triage_bot" in location
    assert "redirect_uri=https%3A%2F%2Ftriage.example.com%2Fzendesk%2Foauth%2Fcallback" in location


@pytest.mark.unit
def test_callback_endpoint_exchanges_code_and_returns_success_html(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("JIRA_API_KEY", "jira")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or")
    monkeypatch.setenv("TRIAGE_ZENDESK_ENABLE_OAUTH", "true")
    monkeypatch.setenv("ZENDESK_BASE_URL", "https://acme.zendesk.com")
    monkeypatch.setenv("ZENDESK_IDENTIFIER", "jira_bug_triage_bot")
    monkeypatch.setenv("ZENDESK_SECRET", "oauth-secret")
    monkeypatch.setenv(
        "ZENDESK_REDIRECT_URI",
        "https://triage.example.com/zendesk/oauth/callback",
    )
    monkeypatch.setenv("ZENDESK_OAUTH_TOKEN_FILE", str(tmp_path / "tokens.json"))
    state = create_oauth_state(secret="oauth-secret")
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "access_token": "stored-access",
                "refresh_token": "stored-refresh",
                "expires_in": 3600,
                "token_type": "bearer",
            },
        )

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        "triage_service.api.zendesk_oauth_routes._build_oauth_http_client",
        lambda _settings: httpx.Client(transport=transport),
    )
    client = TestClient(create_app())
    response = client.get(
        "/zendesk/oauth/callback",
        params={"code": "callback-code", "state": state},
    )
    assert response.status_code == 200
    assert "Zendesk OAuth connected" in response.text
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["code"] == "callback-code"
    stored = ZendeskOAuthTokenStore(tmp_path / "tokens.json").load()
    assert stored is not None
    assert stored.access_token == "stored-access"
