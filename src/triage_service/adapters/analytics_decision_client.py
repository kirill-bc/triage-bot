"""Fire-and-forget HTTP client for triage analytics decision events."""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any, Callable, Protocol

import httpx

from triage_service.core.settings import AppSettings
from triage_service.observability.audit_events import TriageCompletedAuditEvent

LOGGER = logging.getLogger(__name__)


def _start_background_task(
    target: Callable[..., None],
    /,
    *args: object,
    **kwargs: object,
) -> None:
    """Run ``target`` on a background thread so callers return immediately."""
    threading.Thread(target=target, args=args, kwargs=kwargs, daemon=True).start()


def _analytics_log_extra(
    event: TriageCompletedAuditEvent,
    *,
    url: str | None = None,
    status_code: int | None = None,
    error: str | None = None,
    response_preview: str | None = None,
) -> dict[str, Any]:
    extra: dict[str, Any] = {
        "event_type": "analytics_decision",
        "run_id": event.run_id,
        "issue_key": event.issue_key,
        "project": event.project,
    }
    if url is not None:
        extra["url"] = url
    if status_code is not None:
        extra["status_code"] = status_code
    if error is not None:
        extra["error"] = error
    if response_preview is not None:
        extra["response_preview"] = response_preview
    return extra


class AnalyticsDecisionClient(Protocol):
    """Posts completed-triage decision payloads to the analytics dashboard."""

    def emit_completed_decision(
        self,
        event: TriageCompletedAuditEvent,
        *,
        applied_type_change: bool,
        applied_priority_change: bool,
        inference_cost_usd: float | None,
        triaged_at: datetime | None = None,
        issue_created_at: str | None = None,
        issue_name: str | None = None,
    ) -> None:
        """Send one decision row; must never raise to callers."""


def format_iso8601_utc_z(when: datetime) -> str:
    """Format a datetime as ISO-8601 UTC with a ``Z`` suffix (dashboard contract)."""
    if when.tzinfo is None:
        utc = when.replace(tzinfo=timezone.utc)
    else:
        utc = when.astimezone(timezone.utc)
    iso = utc.replace(microsecond=0).isoformat()
    if iso.endswith("+00:00"):
        return iso[:-6] + "Z"
    return iso


def build_decision_payload(
    event: TriageCompletedAuditEvent,
    *,
    applied_type_change: bool,
    applied_priority_change: bool,
    inference_cost_usd: float | None,
    triaged_at: datetime | None = None,
    issue_created_at: str | None = None,
    issue_name: str | None = None,
) -> dict[str, Any]:
    """Map a completed audit event to the dashboard ``POST /decisions`` body."""
    telemetry = event.telemetry or {}
    intake_issue_type = telemetry.get("intake_issue_type")
    intake_priority = telemetry.get("intake_priority")
    if not isinstance(intake_issue_type, str):
        msg = "telemetry.intake_issue_type must be a string for analytics payload"
        raise ValueError(msg)
    if intake_priority is not None and not isinstance(intake_priority, str):
        msg = "telemetry.intake_priority must be a string or null for analytics payload"
        raise ValueError(msg)
    when = triaged_at or datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "event_type": event.event_type,
        "run_id": event.run_id,
        "issue_key": event.issue_key,
        "project": event.project,
        "source": event.source,
        "intake_issue_type": intake_issue_type,
        "intake_priority": intake_priority,
        "recommended_issue_type": event.recommended_issue_type,
        "recommended_priority": event.recommended_priority,
        "applied_type_change": applied_type_change,
        "applied_priority_change": applied_priority_change,
        "confidence": event.confidence,
        "reason": event.reason,
        "triaged_at": format_iso8601_utc_z(when),
    }
    if inference_cost_usd is not None:
        payload["inference_cost_usd"] = inference_cost_usd
    created = (issue_created_at or "").strip()
    if created:
        payload["issue_created_at"] = created
    name = (issue_name or "").strip()
    if name:
        payload["issue_name"] = name
    return payload


class NoOpAnalyticsDecisionClient:
    """Analytics sink that skips HTTP when the dashboard URL is unset."""

    def emit_completed_decision(
        self,
        event: TriageCompletedAuditEvent,
        *,
        applied_type_change: bool,
        applied_priority_change: bool,
        inference_cost_usd: float | None,
        triaged_at: datetime | None = None,
        issue_created_at: str | None = None,
        issue_name: str | None = None,
    ) -> None:
        _ = (
            applied_type_change,
            applied_priority_change,
            inference_cost_usd,
            triaged_at,
            issue_created_at,
            issue_name,
        )
        LOGGER.debug(
            "analytics_decision_skipped",
            extra={
                **_analytics_log_extra(event),
                "event_type": "analytics_decision_skipped",
                "reason": "analytics_disabled",
            },
        )
        return None


class HttpAnalyticsDecisionClient:
    """POST decision payloads to ``ANALYTICS_DASHBOARD_URL/decisions``."""

    def __init__(
        self,
        settings: AppSettings,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self._settings = settings
        self._client = client

    def emit_completed_decision(
        self,
        event: TriageCompletedAuditEvent,
        *,
        applied_type_change: bool,
        applied_priority_change: bool,
        inference_cost_usd: float | None,
        triaged_at: datetime | None = None,
        issue_created_at: str | None = None,
        issue_name: str | None = None,
    ) -> None:
        base_url = str(self._settings.analytics_dashboard_url or "").strip().rstrip("/")
        if not base_url:
            LOGGER.warning(
                "analytics_decision_skipped",
                extra={
                    **_analytics_log_extra(event),
                    "event_type": "analytics_decision_skipped",
                    "reason": "analytics_dashboard_url_empty",
                },
            )
            return None
        try:
            payload = build_decision_payload(
                event,
                applied_type_change=applied_type_change,
                applied_priority_change=applied_priority_change,
                inference_cost_usd=inference_cost_usd,
                triaged_at=triaged_at,
                issue_created_at=issue_created_at,
                issue_name=issue_name,
            )
            url = f"{base_url}/decisions"
            headers: dict[str, str] = {"Content-Type": "application/json"}
            token = str(self._settings.analytics_token or "").strip()
            if token:
                headers["X-Analytics-Token"] = token
            LOGGER.info(
                "analytics_decision_dispatch",
                extra={
                    **_analytics_log_extra(event, url=url),
                    "event_type": "analytics_decision_dispatch",
                },
            )
            _start_background_task(
                self._post_decision,
                url=url,
                payload=payload,
                headers=headers,
                event=event,
            )
        except Exception as exc:
            LOGGER.warning(
                "analytics_decision_emit_failed",
                extra={
                    **_analytics_log_extra(event, error=str(exc)),
                    "event_type": "analytics_decision_emit_failed",
                },
            )
        return None

    def _post_decision(
        self,
        *,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        event: TriageCompletedAuditEvent,
    ) -> None:
        try:
            if self._client is not None:
                response = self._client.post(url, json=payload, headers=headers)
            else:
                timeout = httpx.Timeout(self._settings.analytics_http_timeout_seconds)
                with httpx.Client(timeout=timeout) as client:
                    response = client.post(url, json=payload, headers=headers)
        except Exception as exc:
            LOGGER.warning(
                "analytics_decision_post_failed",
                extra={
                    **_analytics_log_extra(event, url=url, error=str(exc)),
                    "event_type": "analytics_decision_post_failed",
                },
            )
            return None
        if response.status_code >= 400:
            preview = (response.text or "")[:500]
            LOGGER.warning(
                "analytics_decision_post_rejected",
                extra={
                    **_analytics_log_extra(
                        event,
                        url=url,
                        status_code=response.status_code,
                        response_preview=preview or None,
                    ),
                    "event_type": "analytics_decision_post_rejected",
                },
            )
            return None
        LOGGER.info(
            "analytics_decision_posted",
            extra={
                **_analytics_log_extra(event, url=url, status_code=response.status_code),
                "event_type": "analytics_decision_posted",
            },
        )
        return None


def build_analytics_decision_client(
    settings: AppSettings,
    *,
    client: httpx.Client | None = None,
) -> AnalyticsDecisionClient:
    """Return HTTP client when ``ANALYTICS_DASHBOARD_URL`` is set, else no-op."""
    base_url = str(settings.analytics_dashboard_url or "").strip()
    if base_url:
        LOGGER.info(
            "analytics_client_enabled",
            extra={
                "event_type": "analytics_client_enabled",
                "analytics_dashboard_url": base_url.rstrip("/"),
            },
        )
        return HttpAnalyticsDecisionClient(settings, client=client)
    LOGGER.info(
        "analytics_client_disabled",
        extra={
            "event_type": "analytics_client_disabled",
            "reason": "ANALYTICS_DASHBOARD_URL unset",
        },
    )
    return NoOpAnalyticsDecisionClient()
