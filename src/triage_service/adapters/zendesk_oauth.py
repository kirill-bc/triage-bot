"""Zendesk OAuth authorization-code flow helpers and token persistence."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

from triage_service.core.settings import AppSettings

LOGGER = logging.getLogger(__name__)

_OAUTH_CALLBACK_PATH = "/zendesk/oauth/callback"
_TOKEN_REFRESH_BUFFER_SECONDS = 60.0
_TOKEN_FILE_MODE = 0o600


@dataclass(frozen=True)
class ZendeskOAuthTokens:
    """Persisted OAuth token bundle."""

    access_token: str
    refresh_token: str
    expires_at: float


class ZendeskOAuthError(RuntimeError):
    """OAuth configuration, exchange, or refresh failure."""


class ZendeskOAuthTokenStore:
    """JSON file store for Zendesk OAuth access and refresh tokens."""

    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> ZendeskOAuthTokens | None:
        if not self._path.is_file():
            return None
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            LOGGER.warning("Failed to read Zendesk OAuth token file", exc_info=exc)
            return None
        if not isinstance(payload, dict):
            return None
        access_token = str(payload.get("access_token") or "").strip()
        refresh_token = str(payload.get("refresh_token") or "").strip()
        expires_at_raw = payload.get("expires_at")
        if not access_token or not refresh_token or expires_at_raw is None:
            return None
        try:
            expires_at = float(expires_at_raw)
        except (TypeError, ValueError):
            return None
        return ZendeskOAuthTokens(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
        )

    def save(
        self,
        *,
        access_token: str,
        refresh_token: str,
        expires_at: float,
    ) -> None:
        payload = {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": expires_at,
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self._path.with_suffix(f"{self._path.suffix}.tmp")
        temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        temp_path.chmod(_TOKEN_FILE_MODE)
        temp_path.replace(self._path)
        self._path.chmod(_TOKEN_FILE_MODE)

    def clear(self) -> None:
        if self._path.is_file():
            self._path.unlink()


def create_oauth_state(*, secret: str) -> str:
    """Return an HMAC-signed OAuth ``state`` value for CSRF protection."""
    nonce = secrets.token_urlsafe(16)
    signature = hmac.new(
        secret.encode("utf-8"),
        nonce.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{nonce}.{signature}"


def verify_oauth_state(state: str, *, secret: str) -> bool:
    """Validate an OAuth ``state`` value created by :func:`create_oauth_state`."""
    parts = state.split(".", maxsplit=1)
    if len(parts) != 2:
        return False
    nonce, signature = parts
    expected = hmac.new(
        secret.encode("utf-8"),
        nonce.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(signature, expected)


class ZendeskOAuthClient:
    """Exchange authorization codes and refresh Zendesk OAuth access tokens."""

    def __init__(
        self,
        settings: AppSettings,
        *,
        client: httpx.Client | None = None,
        token_store: ZendeskOAuthTokenStore | None = None,
    ) -> None:
        self._settings = settings
        self._client = client
        self._token_store = token_store or ZendeskOAuthTokenStore(
            settings.resolve_zendesk_oauth_token_path(),
        )

    @property
    def token_store(self) -> ZendeskOAuthTokenStore:
        return self._token_store

    def has_tokens(self) -> bool:
        return self._token_store.load() is not None

    def build_authorization_url(self, *, state: str) -> str:
        self._require_oauth_config()
        redirect_uri = self._redirect_uri()
        base_url = self._base_url()
        params = urlencode(
            {
                "response_type": "code",
                "redirect_uri": redirect_uri,
                "client_id": self._client_id(),
                "scope": self._scope(),
                "state": state,
            },
        )
        return f"{base_url}/oauth/authorizations/new?{params}"

    def exchange_authorization_code(self, code: str) -> ZendeskOAuthTokens:
        self._require_oauth_config()
        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": self._client_id(),
            "client_secret": self._client_secret(),
            "redirect_uri": self._redirect_uri(),
            "scope": self._scope(),
        }
        return self._request_tokens(payload)

    def get_access_token(self) -> str:
        tokens = self._token_store.load()
        if tokens is None:
            raise ZendeskOAuthError(
                "Zendesk OAuth tokens are missing; visit /zendesk/oauth/authorize once.",
            )
        if self._token_is_valid(tokens):
            return tokens.access_token
        refreshed = self._refresh_tokens(tokens.refresh_token)
        return refreshed.access_token

    def _refresh_tokens(self, refresh_token: str) -> ZendeskOAuthTokens:
        self._require_oauth_config()
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self._client_id(),
            "client_secret": self._client_secret(),
            "scope": self._scope(),
        }
        return self._request_tokens(payload)

    def _request_tokens(self, payload: dict[str, str]) -> ZendeskOAuthTokens:
        response = self._post_tokens(payload)
        if response.status_code >= 400:
            snippet = response.text[:300]
            raise ZendeskOAuthError(
                f"Zendesk OAuth token request failed with HTTP {response.status_code}: {snippet}",
            )
        body = response.json()
        if not isinstance(body, dict):
            raise ZendeskOAuthError("Zendesk OAuth token response was not a JSON object.")
        tokens = self._parse_token_response(body)
        self._token_store.save(
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token,
            expires_at=tokens.expires_at,
        )
        return tokens

    def _post_tokens(self, payload: dict[str, str]) -> httpx.Response:
        url = f"{self._base_url()}/oauth/tokens"
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self._client is not None:
            return self._client.post(url, headers=headers, json=payload)
        timeout = httpx.Timeout(self._settings.zendesk_http_timeout_seconds)
        with httpx.Client(timeout=timeout) as client:
            return client.post(url, headers=headers, json=payload)

    def _parse_token_response(self, body: dict[str, Any]) -> ZendeskOAuthTokens:
        access_token = str(body.get("access_token") or "").strip()
        refresh_token = str(body.get("refresh_token") or "").strip()
        expires_in_raw = body.get("expires_in")
        if not access_token or not refresh_token:
            raise ZendeskOAuthError("Zendesk OAuth token response missing access/refresh token.")
        expires_in = 3600.0
        if isinstance(expires_in_raw, (int, float, str)):
            try:
                expires_in = float(expires_in_raw)
            except ValueError:
                expires_in = 3600.0
        expires_at = time.time() + max(expires_in, 60.0)
        return ZendeskOAuthTokens(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
        )

    @staticmethod
    def _token_is_valid(tokens: ZendeskOAuthTokens) -> bool:
        return tokens.expires_at > (time.time() + _TOKEN_REFRESH_BUFFER_SECONDS)

    def _require_oauth_config(self) -> None:
        if not self._settings.zendesk_oauth_configured:
            raise ZendeskOAuthError(
                "Zendesk OAuth is not configured; set TRIAGE_ZENDESK_ENABLE_OAUTH=true, "
                "ZENDESK_IDENTIFIER, ZENDESK_SECRET, ZENDESK_BASE_URL or ZENDESK_SUBDOMAIN, "
                "and ZENDESK_REDIRECT_URI or TRIAGE_PUBLIC_BASE_URL.",
            )

    def _base_url(self) -> str:
        base = self._settings.resolve_zendesk_base_url()
        if not base:
            raise ZendeskOAuthError("Zendesk base URL is required for OAuth.")
        return base

    def _redirect_uri(self) -> str:
        redirect_uri = self._settings.resolve_zendesk_oauth_redirect_uri()
        if not redirect_uri:
            raise ZendeskOAuthError(
                "Zendesk OAuth redirect URI is required; set ZENDESK_REDIRECT_URI or "
                "TRIAGE_PUBLIC_BASE_URL.",
            )
        return redirect_uri

    def _client_id(self) -> str:
        client_id = str(self._settings.zendesk_identifier or "").strip()
        if not client_id:
            raise ZendeskOAuthError("ZENDESK_IDENTIFIER is required for OAuth.")
        return client_id

    def _client_secret(self) -> str:
        secret = str(self._settings.zendesk_secret or "").strip()
        if not secret:
            raise ZendeskOAuthError("ZENDESK_SECRET is required for OAuth.")
        return secret

    def _scope(self) -> str:
        return str(self._settings.zendesk_oauth_scope or "read").strip() or "read"


def build_zendesk_oauth_client(settings: AppSettings) -> ZendeskOAuthClient | None:
    """Return an OAuth client when OAuth mode is enabled."""
    if not settings.triage_zendesk_enable_oauth:
        return None
    return ZendeskOAuthClient(settings)


def oauth_callback_path() -> str:
    """Public OAuth callback path registered with Zendesk."""
    return _OAUTH_CALLBACK_PATH
