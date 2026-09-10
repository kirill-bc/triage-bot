"""Deliver triage outcomes to a Jira Automation incoming webhook.

The service keeps every decision and all comment copy; the Automation rule is a dumb applier
that maps ``{{webhookData.*}}`` onto label, comment, and field-edit actions. Because the rule
runs as the Automation actor, its edits do not re-trigger other rules — which is what makes
re-triage on priority change safe.
"""

from __future__ import annotations

import logging
from typing import Any, cast
from urllib.parse import urlsplit

import httpx

from triage_service.adapters.jira_http_retry import (
    TransportRetriesExhausted,
    request_with_retries,
)
from triage_service.adapters.jira_issue_fetcher import FetchedIssue
from triage_service.adapters.triage_outcome_rendering import (
    AutoApplyPolicy,
    OutcomeDecision,
    build_outcome_decision,
    render_plain_text_comment,
)
from triage_service.core.settings import AppSettings
from triage_service.core.triage_action_applied import TriageActionAppliedFlags
from triage_service.core.triage_fallback import TriageFailure
from triage_service.core.triage_recommendation_parser import TriageRecommendation
from triage_service.observability.audit_events import (
    OutcomeDeliveredAuditEvent,
    TriageSourceLiteral,
)
from triage_service.observability.audit_store import AuditStore

LOGGER = logging.getLogger(__name__)

# Bump when the payload contract changes shape; Rule B reads this to stay compatible.
CALLBACK_PAYLOAD_VERSION = 1

_TOKEN_HEADER = "X-Automation-Webhook-Token"

# Callers may name their own Rule B webhook, so the callback target is pinned to the hosts
# Atlassian serves Automation webhooks from. Operator-supplied env URLs are trusted as-is.
ALLOWED_WEBHOOK_HOSTS = frozenset({"automation.atlassian.com", "api-private.atlassian.com"})


def validate_request_webhook_url(url: str) -> str:
    """Return the trimmed caller-supplied callback URL, or raise ``ValueError`` if unsafe."""
    candidate = (url or "").strip()
    if not candidate:
        msg = "Automation webhook URL must not be empty"
        raise ValueError(msg)
    parsed = urlsplit(candidate)
    if parsed.scheme != "https":
        msg = f"Automation webhook URL must use https, got {parsed.scheme or 'no scheme'!r}"
        raise ValueError(msg)
    if parsed.hostname not in ALLOWED_WEBHOOK_HOSTS:
        allowed = ", ".join(sorted(ALLOWED_WEBHOOK_HOSTS))
        msg = (
            f"Automation webhook host {parsed.hostname!r} is not allowed; "
            f"expected one of: {allowed}"
        )
        raise ValueError(msg)
    return candidate


class AutomationCallbackError(RuntimeError):
    """Raised when the outcome callback to Jira Automation cannot be delivered."""


def build_callback_payload(
    *,
    issue: FetchedIssue,
    issue_key: str,
    project: str,
    source: str,
    recommendation: TriageRecommendation,
    run_id: str,
    decision: OutcomeDecision,
    post_comment: bool,
) -> dict[str, Any]:
    """Render the versioned outcome payload Rule B applies.

    ``issues`` is Jira Automation's work-item binding for the incoming-webhook trigger option
    "Issues provided in the webhook HTTP POST body". Extra fields become ``{{webhookData.*}}``.
    ``comment.body`` uses the "applied" copy whenever the payload directs a field change, since
    Rule B performs those edits in the same run.
    """
    mutations_directed = decision.apply_bug_to_story or decision.apply_priority is not None
    body = (
        render_plain_text_comment(
            issue,
            recommendation,
            mutations_applied=mutations_directed,
        )
        if post_comment
        else None
    )
    apply_priority = (
        {
            "from": decision.apply_priority.from_priority,
            "to": decision.apply_priority.to_priority,
        }
        if decision.apply_priority is not None
        else None
    )
    return {
        "payload_version": CALLBACK_PAYLOAD_VERSION,
        "issues": [issue_key],
        "run_id": run_id,
        "issue_key": issue_key,
        "project": project,
        "source": source,
        "recommendation": {
            "issue_type": recommendation.recommended_issue_type,
            "priority": recommendation.recommended_priority,
            "confidence": recommendation.confidence,
        },
        "labels": list(decision.labels),
        "comment": {"post": post_comment, "body": body},
        "actions": {
            "apply_bug_to_story": decision.apply_bug_to_story,
            "apply_priority": apply_priority,
        },
    }


class AutomationWebhookTriageActionExecutor:
    """POST the rendered outcome payload to the Jira Automation applier rule.

    ``TriageActionAppliedFlags`` returned here mean **apply directed**, not apply confirmed: the
    service no longer observes the write, so analytics rows describe what was requested of Jira
    Automation.

    ``webhook_url``/``webhook_token`` let a caller name the Rule B endpoint per run (each project
    scopes its own Automation rule); they take precedence over ``JIRA_AUTOMATION_WEBHOOK_URL`` and
    ``JIRA_AUTOMATION_WEBHOOK_TOKEN``.
    """

    def __init__(
        self,
        settings: AppSettings,
        *,
        client: httpx.Client | None = None,
        post_mismatch_comments: bool = True,
        auto_apply_deescalation: bool | None = None,
        auto_apply_escalation: bool | None = None,
        auto_apply_bug_to_story: bool | None = None,
        audit_store: AuditStore | None = None,
        webhook_token: str | None = None,
        webhook_url: str | None = None,
    ) -> None:
        self._settings = settings
        self._client = client
        self._post_mismatch_comments = post_mismatch_comments
        self._audit_store = audit_store
        self._webhook_token = (webhook_token or "").strip() or None
        self._webhook_url = (webhook_url or "").strip() or None
        self._policy = AutoApplyPolicy(
            apply_deescalation=(
                settings.triage_auto_apply_deescalation
                if auto_apply_deescalation is None
                else auto_apply_deescalation
            ),
            apply_escalation=(
                settings.triage_auto_apply_escalation
                if auto_apply_escalation is None
                else auto_apply_escalation
            ),
            apply_bug_to_story=(
                settings.triage_auto_apply_bug_to_story
                if auto_apply_bug_to_story is None
                else auto_apply_bug_to_story
            ),
        )

    def apply_triage_outcome(
        self,
        *,
        issue: FetchedIssue | None,
        issue_key: str,
        project: str,
        source: str,
        outcome: TriageRecommendation | TriageFailure,
        run_id: str,
    ) -> TriageActionAppliedFlags:
        """``issue`` is None when triage failed before fetch or fetch failed."""
        if isinstance(outcome, TriageFailure) or issue is None:
            return TriageActionAppliedFlags()
        decision = build_outcome_decision(issue, outcome, policy=self._policy)
        payload = build_callback_payload(
            issue=issue,
            issue_key=issue_key,
            project=project,
            source=source,
            recommendation=outcome,
            run_id=run_id,
            decision=decision,
            post_comment=self._post_mismatch_comments and decision.post_comment,
        )
        self._deliver(
            payload,
            issue_key=issue_key,
            project=project,
            source=source,
            run_id=run_id,
        )
        return TriageActionAppliedFlags(
            applied_type_change=decision.apply_bug_to_story,
            applied_priority_change=decision.apply_priority is not None,
        )

    def _resolve_url(self) -> str:
        """Prefer the caller-supplied endpoint, re-validating it at the point of use."""
        if self._webhook_url is not None:
            try:
                return validate_request_webhook_url(self._webhook_url)
            except ValueError as exc:
                raise AutomationCallbackError(str(exc)) from exc
        url = str(self._settings.jira_automation_webhook_url or "").strip()
        if not url:
            msg = (
                "Automation callback requires JIRA_AUTOMATION_WEBHOOK_URL "
                "when applying outcomes via Jira Automation."
            )
            raise AutomationCallbackError(msg)
        return url

    def _deliver(
        self,
        payload: dict[str, Any],
        *,
        issue_key: str,
        project: str,
        source: str,
        run_id: str,
    ) -> None:
        url = self._resolve_url()
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        token = (
            self._webhook_token
            or str(self._settings.jira_automation_webhook_token or "").strip()
        )
        if token:
            headers[_TOKEN_HEADER] = token

        try:
            response, attempts = self._post(url, payload=payload, headers=headers)
        except (TransportRetriesExhausted, httpx.RequestError) as exc:
            failure = f"Automation callback request failed after retries: {exc}"
            self._record_delivery(
                issue_key=issue_key,
                project=project,
                source=source,
                run_id=run_id,
                delivered=False,
                http_status=None,
                attempts=getattr(exc, "attempts", 0),
                failure=failure,
            )
            raise AutomationCallbackError(failure) from exc

        if response.is_error:
            snippet = response.text[:300]
            failure = (
                f"Automation callback failed with HTTP {response.status_code}: {snippet}"
            )
            self._record_delivery(
                issue_key=issue_key,
                project=project,
                source=source,
                run_id=run_id,
                delivered=False,
                http_status=response.status_code,
                attempts=attempts,
                failure=failure,
            )
            raise AutomationCallbackError(failure)

        self._record_delivery(
            issue_key=issue_key,
            project=project,
            source=source,
            run_id=run_id,
            delivered=True,
            http_status=response.status_code,
            attempts=attempts,
            failure=None,
        )

    def _post(
        self,
        url: str,
        *,
        payload: dict[str, Any],
        headers: dict[str, str],
    ) -> tuple[httpx.Response, int]:
        if self._client is not None:
            return request_with_retries(
                self._client,
                "POST",
                url,
                max_retries=self._settings.jira_http_max_retries,
                headers=headers,
                json=payload,
            )
        timeout = httpx.Timeout(self._settings.jira_automation_webhook_timeout_seconds)
        with httpx.Client(timeout=timeout) as client:
            return request_with_retries(
                client,
                "POST",
                url,
                max_retries=self._settings.jira_http_max_retries,
                headers=headers,
                json=payload,
            )

    def _record_delivery(
        self,
        *,
        issue_key: str,
        project: str,
        source: str,
        run_id: str,
        delivered: bool,
        http_status: int | None,
        attempts: int,
        failure: str | None,
    ) -> None:
        log_extra = {
            "event_type": "outcome_delivered",
            "run_id": run_id,
            "issue_key": issue_key,
            "project": project,
            "delivery_mode": "automation_webhook",
            "delivered": delivered,
            "http_status": http_status,
            "attempts": attempts,
        }
        if delivered:
            LOGGER.info("outcome_delivered", extra=log_extra)
        else:
            LOGGER.warning("outcome_delivery_failed", extra={**log_extra, "error": failure})
        if self._audit_store is None:
            return
        try:
            self._audit_store.record(
                OutcomeDeliveredAuditEvent(
                    event_type="outcome_delivered",
                    run_id=run_id,
                    issue_key=issue_key,
                    project=project,
                    source=cast(TriageSourceLiteral, source),
                    delivery_mode="automation_webhook",
                    delivered=delivered,
                    http_status=http_status,
                    attempts=attempts,
                    failure=failure,
                ),
            )
        except Exception:
            # Audit emission must never mask the delivery result.
            LOGGER.warning("outcome_delivered audit emission failed", exc_info=True)
