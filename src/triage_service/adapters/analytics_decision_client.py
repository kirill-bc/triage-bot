"""Fire-and-forget HTTP client for triage analytics decision events."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Protocol

import httpx

from triage_service.core.settings import AppSettings
from triage_service.observability.audit_events import TriageCompletedAuditEvent

LOGGER = logging.getLogger(__name__)


class AnalyticsDecisionClient(Protocol):
    """Posts completed-triage decision payloads to the analytics dashboard."""

    def emit_completed_decision(
        self,
        event: TriageCompletedAuditEvent,
        *,
        applied_type_change: bool,
        applied_priority_change: bool,
        inference_cost_usd: float | None,
        occurred_at: datetime | None = None,
    ) -> None:
        """Send one decision row; must never raise to callers."""


def build_decision_payload(
    event: TriageCompletedAuditEvent,
    *,
    applied_type_change: bool,
    applied_priority_change: bool,
    inference_cost_usd: float | None,
    occurred_at: datetime | None = None,
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
    when = occurred_at or datetime.now(timezone.utc)
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
        "occurred_at": when.isoformat(),
    }
    if inference_cost_usd is not None:
        payload["inference_cost_usd"] = inference_cost_usd
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
        occurred_at: datetime | None = None,
    ) -> None:
        _ = (event, applied_type_change, applied_priority_change, inference_cost_usd, occurred_at)
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
        occurred_at: datetime | None = None,
    ) -> None:
        base_url = str(self._settings.analytics_dashboard_url or "").strip().rstrip("/")
        if not base_url:
            return None
        payload = build_decision_payload(
            event,
            applied_type_change=applied_type_change,
            applied_priority_change=applied_priority_change,
            inference_cost_usd=inference_cost_usd,
            occurred_at=occurred_at,
        )
        url = f"{base_url}/decisions"
        headers: dict[str, str] = {"Content-Type": "application/json"}
        token = str(self._settings.analytics_token or "").strip()
        if token:
            headers["X-Analytics-Token"] = token
        try:
            if self._client is not None:
                self._client.post(url, json=payload, headers=headers)
                return None
            timeout = httpx.Timeout(self._settings.analytics_http_timeout_seconds)
            with httpx.Client(timeout=timeout) as client:
                client.post(url, json=payload, headers=headers)
        except Exception as exc:
            LOGGER.warning(
                "analytics_decision_post_failed",
                extra={
                    "event_type": "analytics_decision_post_failed",
                    "run_id": event.run_id,
                    "issue_key": event.issue_key,
                    "project": event.project,
                    "error": str(exc),
                },
            )
        return None


def build_analytics_decision_client(
    settings: AppSettings,
    *,
    client: httpx.Client | None = None,
) -> AnalyticsDecisionClient:
    """Return HTTP client when ``ANALYTICS_DASHBOARD_URL`` is set, else no-op."""
    if str(settings.analytics_dashboard_url or "").strip():
        return HttpAnalyticsDecisionClient(settings, client=client)
    return NoOpAnalyticsDecisionClient()
