"""HTTP routes for Zendesk OAuth setup and callback handling."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

from triage_service.adapters.zendesk_oauth import (
    ZendeskOAuthClient,
    ZendeskOAuthError,
    build_zendesk_oauth_client,
    create_oauth_state,
    oauth_callback_path,
    verify_oauth_state,
)
from triage_service.core.settings import AppSettings, load_settings

router = APIRouter(prefix="/zendesk/oauth", tags=["zendesk-oauth"])


class ZendeskOAuthRedirectUriResponse(BaseModel):
    """Values to register in Zendesk Admin Center."""

    redirect_uri: str = Field(min_length=1)
    authorize_path: str = Field(default="/zendesk/oauth/authorize")
    callback_path: str = Field(default_factory=oauth_callback_path)


def _build_oauth_http_client(settings: AppSettings) -> httpx.Client:
    timeout = httpx.Timeout(settings.zendesk_http_timeout_seconds)
    return httpx.Client(timeout=timeout)


def _load_app_settings() -> AppSettings:
    return load_settings()


def _require_oauth_enabled(settings: AppSettings = Depends(_load_app_settings)) -> AppSettings:
    if not settings.triage_zendesk_enable_oauth:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Zendesk OAuth is disabled (TRIAGE_ZENDESK_ENABLE_OAUTH=false).",
        )
    return settings


def _oauth_client(settings: AppSettings) -> ZendeskOAuthClient:
    client = build_zendesk_oauth_client(settings)
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Zendesk OAuth is disabled.",
        )
    return client


def _oauth_state_secret(settings: AppSettings) -> str:
    secret = str(settings.zendesk_secret or "").strip()
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ZENDESK_SECRET is required for OAuth state validation.",
        )
    return secret


@router.get("/redirect-uri", response_model=ZendeskOAuthRedirectUriResponse)
def get_redirect_uri(
    settings: AppSettings = Depends(_require_oauth_enabled),
) -> ZendeskOAuthRedirectUriResponse:
    """Return the OAuth redirect URI to register in Zendesk Admin Center."""
    redirect_uri = settings.resolve_zendesk_oauth_redirect_uri()
    if not redirect_uri:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Zendesk OAuth redirect URI is not configured; set ZENDESK_REDIRECT_URI or "
                "TRIAGE_PUBLIC_BASE_URL."
            ),
        )
    return ZendeskOAuthRedirectUriResponse(redirect_uri=redirect_uri)


@router.get("/authorize")
def start_authorization(
    settings: AppSettings = Depends(_require_oauth_enabled),
) -> RedirectResponse:
    """Redirect an operator to Zendesk to grant OAuth access once."""
    oauth = _oauth_client(settings)
    state = create_oauth_state(secret=_oauth_state_secret(settings))
    try:
        authorization_url = oauth.build_authorization_url(state=state)
    except ZendeskOAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    return RedirectResponse(url=authorization_url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)


@router.get("/callback", response_class=HTMLResponse)
def oauth_callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
    settings: AppSettings = Depends(_require_oauth_enabled),
) -> HTMLResponse:
    """Handle Zendesk redirect after the operator grants OAuth access."""
    if error:
        detail = error_description or error
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Zendesk OAuth authorization failed: {detail}",
        )
    if not code or not state:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing OAuth authorization code or state.",
        )
    if not verify_oauth_state(state, secret=_oauth_state_secret(settings)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid OAuth state.",
        )
    with _build_oauth_http_client(settings) as http_client:
        oauth_with_client = ZendeskOAuthClient(settings, client=http_client)
        try:
            oauth_with_client.exchange_authorization_code(code)
        except ZendeskOAuthError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=str(exc),
            ) from exc
    return HTMLResponse(
        content=(
            "<html><body><h1>Zendesk OAuth connected</h1>"
            "<p>Triage can now use OAuth to fetch linked Zendesk tickets.</p>"
            "<p>You can close this tab.</p></body></html>"
        ),
        status_code=status.HTTP_200_OK,
    )
