"""Zendesk OAuth client-credentials flow with in-memory token caching."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from triage_service.core.settings import AppSettings

LOGGER = logging.getLogger(__name__)

_TOKEN_REFRESH_BUFFER_SECONDS = 60.0


@dataclass(frozen=True)
class ZendeskOAuthTokens:
    """In-memory OAuth access token bundle."""

    access_token: str
    expires_at: float


class ZendeskOAuthError(RuntimeError):
    """OAuth configuration or token mint failure."""


class ZendeskOAuthClient:
    """Mint and cache Zendesk OAuth access tokens via client_credentials."""

    def __init__(
        self,
        settings: AppSettings,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self._settings = settings
        self._client = client
        self._cached_token: ZendeskOAuthTokens | None = None

    def get_access_token(self) -> str:
        if self._cached_token is not None and self._token_is_valid(self._cached_token):
            return self._cached_token.access_token
        tokens = self._mint_tokens()
        self._cached_token = tokens
        return tokens.access_token

    def clear_cached_token(self) -> None:
        """Drop the in-memory token so the next request remints."""
        self._cached_token = None

    def _mint_tokens(self) -> ZendeskOAuthTokens:
        self._require_oauth_config()
        payload = {
            "grant_type": "client_credentials",
            "client_id": self._client_id(),
            "client_secret": self._client_secret(),
            "scope": self._scope(),
        }
        response = self._post_tokens(payload)
        if response.status_code >= 400:
            snippet = response.text[:300]
            raise ZendeskOAuthError(
                f"Zendesk OAuth token request failed with HTTP {response.status_code}: {snippet}",
            )
        body = response.json()
        if not isinstance(body, dict):
            raise ZendeskOAuthError("Zendesk OAuth token response was not a JSON object.")
        return self._parse_token_response(body)

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
        expires_in_raw = body.get("expires_in")
        if not access_token:
            raise ZendeskOAuthError("Zendesk OAuth token response missing access token.")
        expires_in = 3600.0
        if isinstance(expires_in_raw, (int, float, str)):
            try:
                expires_in = float(expires_in_raw)
            except ValueError:
                expires_in = 3600.0
        expires_at = time.time() + max(expires_in, 60.0)
        return ZendeskOAuthTokens(
            access_token=access_token,
            expires_at=expires_at,
        )

    @staticmethod
    def _token_is_valid(tokens: ZendeskOAuthTokens) -> bool:
        return tokens.expires_at > (time.time() + _TOKEN_REFRESH_BUFFER_SECONDS)

    def _require_oauth_config(self) -> None:
        if not self._settings.zendesk_oauth_configured:
            raise ZendeskOAuthError(
                "Zendesk OAuth is not configured; set TRIAGE_ZENDESK_ENABLE_OAUTH=true, "
                "ZENDESK_IDENTIFIER, ZENDESK_SECRET, and ZENDESK_BASE_URL or ZENDESK_SUBDOMAIN.",
            )

    def _base_url(self) -> str:
        base = self._settings.resolve_zendesk_base_url()
        if not base:
            raise ZendeskOAuthError("Zendesk base URL is required for OAuth.")
        return base

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
