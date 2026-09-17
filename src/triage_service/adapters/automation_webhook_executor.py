"""Deliver triage outcomes to a Jira Automation incoming webhook.

The service keeps every decision; the Automation rule maps ``{{webhookData.*}}`` onto label
and field-edit actions, and writes the comment text itself from the inputs in ``comment``.
Because the rule runs as the Automation actor, its edits do not re-trigger other rules —
which is what makes re-triage on priority change safe.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
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
CALLBACK_PAYLOAD_VERSION = 2

_TOKEN_HEADER = "X-Automation-Webhook-Token"

REDACTED_URL_MARKER = "<redacted-webhook-url>"

# Callers may name their own Rule B webhook, so the callback target is pinned to the current
# Atlassian Automation webhook host. Request URLs and the settings fallback share this check.
ALLOWED_WEBHOOK_HOSTS = frozenset({"api-private.atlassian.com"})
_HTTPS_PORT = 443


def validate_request_webhook_url(url: str) -> str:
    """Return the trimmed caller-supplied callback URL, or raise ``ValueError`` if unsafe.

    Error messages stay generic (scheme, allowlist, port). They never include the rejected
    value: ``urlsplit`` can park a percent-encoded path in ``hostname``, and that path is
    the webhook secret.
    """
    candidate = (url or "").strip()
    if not candidate:
        msg = "Automation webhook URL must not be empty"
        raise ValueError(msg)
    parsed = urlsplit(candidate)
    if parsed.scheme != "https":
        # Do not echo scheme or any other URL fragment: percent-encoded paths can
        # appear in unexpected ``urlsplit`` fields and are a secret on this credential.
        msg = "Automation webhook URL must use https"
        raise ValueError(msg)
    # ``port`` raises on a non-numeric or out-of-range value, which httpx would otherwise
    # surface as an unhandled InvalidURL at request time, after triage has already run.
    try:
        port = parsed.port
    except ValueError as exc:
        msg = "Automation webhook URL has a malformed port"
        raise ValueError(msg) from exc
    if parsed.hostname not in ALLOWED_WEBHOOK_HOSTS:
        allowed = ", ".join(sorted(ALLOWED_WEBHOOK_HOSTS))
        msg = f"Automation webhook host is not allowed; expected one of: {allowed}"
        raise ValueError(msg)
    if port is not None and port != _HTTPS_PORT:
        msg = f"Automation webhook port {port} is not allowed; expected {_HTTPS_PORT}"
        raise ValueError(msg)
    return candidate


@dataclass(frozen=True)
class WebhookCallbackCredentials:
    """One Rule B webhook URL and its matching token, already validated as a pair."""

    url: str
    token: str


class WebhookCallbackConfigError(ValueError):
    """Callback URL/token pairing, fallback completeness, or URL validation failed.

    The HTTP API maps this to 422. Handler construction raises it as ``ValueError`` so
    non-HTTP callers fail before Jira fetch and model inference.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


_REQUEST_URL_WITHOUT_TOKEN = (
    "Automation callback webhook_url requires the matching webhook_token: "
    "per-request callback credentials must be sent together"
)
_REQUEST_TOKEN_WITHOUT_URL = (
    "Automation callback webhook_token requires the matching webhook_url: "
    "per-request callback credentials must be sent together"
)
_MISSING_CALLBACK_PAIR = (
    "Automation callback requires a webhook URL and token together: send both "
    "per-request values, or configure both JIRA_AUTOMATION_WEBHOOK_URL and "
    "JIRA_AUTOMATION_WEBHOOK_TOKEN"
)


def resolve_webhook_callback_credentials(
    *,
    request_url: str | None,
    request_token: str | None,
    settings: AppSettings | None,
) -> WebhookCallbackCredentials:
    """Return one complete callback pair, or raise ``WebhookCallbackConfigError``.

    URL and token address the same Rule B webhook, so they never mix across sources: a
    per-request URL is paired only with a per-request token, and the settings pair is
    used only when the request supplies neither. The URL that will actually be used is
    validated here so a bad value fails before triage work is paid for.
    """
    url = str(request_url or "").strip() or None
    token = str(request_token or "").strip() or None
    if token is not None and url is None:
        raise WebhookCallbackConfigError("request_token_without_url", _REQUEST_TOKEN_WITHOUT_URL)
    if url is not None:
        if token is None:
            raise WebhookCallbackConfigError(
                "request_url_without_token",
                _REQUEST_URL_WITHOUT_TOKEN,
            )
        try:
            return WebhookCallbackCredentials(
                url=validate_request_webhook_url(url),
                token=token,
            )
        except ValueError as exc:
            raise WebhookCallbackConfigError("invalid_url", str(exc)) from exc
    if settings is None:
        raise WebhookCallbackConfigError("missing_pair", _MISSING_CALLBACK_PAIR)
    config_error = configured_webhook_config_error(settings)
    if config_error is not None:
        raise WebhookCallbackConfigError("configured_pair", config_error)
    fallback_url = str(settings.jira_automation_webhook_url or "").strip()
    fallback_token = str(settings.jira_automation_webhook_token or "").strip()
    if not fallback_url or not fallback_token:
        raise WebhookCallbackConfigError("missing_pair", _MISSING_CALLBACK_PAIR)
    return WebhookCallbackCredentials(url=fallback_url, token=fallback_token)


def configured_webhook_config_error(settings: AppSettings) -> str | None:
    """Describe why the configured callback fallback is unusable, or None when absent or valid.

    ``JIRA_AUTOMATION_WEBHOOK_URL`` and ``JIRA_AUTOMATION_WEBHOOK_TOKEN`` name one Rule B
    webhook: they must be set together, and the URL must satisfy the same validation as a
    per-request URL. Both unset is valid — requests may carry their own pair. The API
    readiness probe and ``resolve_webhook_callback_credentials`` share this check so a
    misconfigured Secret fails before a triage run is paid for. Returned messages never
    include the URL itself because its path is a secret.
    """
    url = str(settings.jira_automation_webhook_url or "").strip() or None
    token = str(settings.jira_automation_webhook_token or "").strip() or None
    if url is None and token is None:
        return None
    if token is None:
        return (
            "JIRA_AUTOMATION_WEBHOOK_URL is set without JIRA_AUTOMATION_WEBHOOK_TOKEN: "
            "the configured callback credentials must be set together"
        )
    if url is None:
        return (
            "JIRA_AUTOMATION_WEBHOOK_TOKEN is set without JIRA_AUTOMATION_WEBHOOK_URL: "
            "the configured callback credentials must be set together"
        )
    try:
        validate_request_webhook_url(url)
    except ValueError as exc:
        return f"Configured JIRA_AUTOMATION_WEBHOOK_URL is invalid: {exc}"
    return None


def redacted_webhook_url(url: str) -> str:
    """Return the callback origin only when its host is an allowed callback host.

    Callers redact unvalidated input (inbound debug logging runs before request validation),
    so this must never raise and must never trust the parsed authority: percent-encoded
    slashes survive ``urlsplit`` as part of ``hostname``, so a value like
    ``https://host%2Fsecret`` would otherwise re-expose the secret path as its "origin".
    Anything that is not exactly an allowed host collapses to the fixed marker.
    """
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
    except ValueError:
        return REDACTED_URL_MARKER
    if parsed.scheme and hostname in ALLOWED_WEBHOOK_HOSTS:
        return f"{parsed.scheme}://{hostname}"
    return REDACTED_URL_MARKER


def delivery_attempts(exc: object) -> int:
    """HTTP attempts for a failed callback; bare transport errors still made one request."""
    return int(getattr(exc, "attempts", 1))


class AutomationCallbackError(RuntimeError):
    """Raised when the outcome callback to Jira Automation cannot be delivered."""


def _issue_priority(issue: FetchedIssue) -> str | None:
    """Current Jira priority as a bare value; Rule B supplies the copy for the unset case."""
    if issue.priority is None:
        return None
    return str(issue.priority).strip() or None


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

    ``comment`` carries composition inputs so an updated Rule B can write the text, mention
    the reporter natively, and render wiki-markup links. ``kind`` selects the advisory or
    applied wording — "applied" whenever the payload directs a field change, since Rule B
    performs those edits in the same run — and ``topic`` selects the issue-type or priority
    wording. Both are flat scalars because Automation's ``{{#if}}`` is unreliable against
    nested nulls such as ``actions.apply_priority``.

    ``comment.body`` is the pre-rendered v1 copy. It stays on v2 so a Rule B that still
    posts ``{{webhookData.comment.body}}`` cannot accept the callback and write an empty
    comment; drop it only after every consumer has switched to the composition fields.
    """
    mutations_directed = decision.apply_bug_to_story or decision.apply_priority is not None
    apply_priority = (
        {
            "from": decision.apply_priority.from_priority,
            "to": decision.apply_priority.to_priority,
        }
        if decision.apply_priority is not None
        else None
    )
    body = (
        render_plain_text_comment(
            issue,
            recommendation,
            mutations_applied=mutations_directed,
        )
        if post_comment
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
        "comment": {
            "post": post_comment,
            "body": body,
            "kind": "applied" if mutations_directed else "advisory",
            "topic": (
                "issue_type"
                if recommendation.recommended_issue_type == "Story"
                else "priority"
            ),
            "reason": recommendation.reason,
            "current_priority": _issue_priority(issue),
        },
        "actions": {
            "apply_bug_to_story": decision.apply_bug_to_story,
            "apply_priority": apply_priority,
        },
    }


class AutomationWebhookTriageActionExecutor:
    """POST the outcome decision payload to the Jira Automation applier rule.

    ``TriageActionAppliedFlags`` returned here mean **apply directed**, not apply confirmed: the
    service no longer observes the write, so analytics rows describe what was requested of Jira
    Automation.

    ``webhook_url``/``webhook_token`` are the already-resolved Rule B endpoint for this run.
    Pairing, source precedence, and URL validation happen in
    ``resolve_webhook_callback_credentials`` before this executor is constructed.
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
        webhook_token: str,
        webhook_url: str,
    ) -> None:
        self._settings = settings
        self._client = client
        self._post_mismatch_comments = post_mismatch_comments
        self._audit_store = audit_store
        self._webhook_token = webhook_token.strip()
        self._webhook_url = webhook_url.strip()
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

    def _callback_target(self) -> tuple[str, str]:
        """Return the injected callback URL after a last-mile host/scheme check."""
        try:
            return validate_request_webhook_url(self._webhook_url), self._webhook_token
        except ValueError as exc:
            raise AutomationCallbackError(str(exc)) from exc

    def _deliver(
        self,
        payload: dict[str, Any],
        *,
        issue_key: str,
        project: str,
        source: str,
        run_id: str,
    ) -> None:
        try:
            url, token = self._callback_target()
        except AutomationCallbackError as exc:
            self._record_delivery(
                issue_key=issue_key,
                project=project,
                source=source,
                run_id=run_id,
                delivered=False,
                http_status=None,
                attempts=0,
                failure=str(exc),
            )
            raise
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            _TOKEN_HEADER: token,
        }

        try:
            response, attempts = self._post(url, payload=payload, headers=headers)
        except (TransportRetriesExhausted, httpx.RequestError) as exc:
            # Exception text is untrusted here: httpx includes the request URL, and
            # TransportRetriesExhausted wraps that cause. Persist the type only, and
            # chain from a type-only surrogate so LOGGER.exception cannot print the
            # webhook path.
            cause = getattr(exc, "cause", exc)
            failure = f"Automation callback request failed: {type(cause).__name__}"
            self._record_delivery(
                issue_key=issue_key,
                project=project,
                source=source,
                run_id=run_id,
                delivered=False,
                http_status=None,
                attempts=delivery_attempts(exc),
                failure=failure,
            )
            raise AutomationCallbackError(failure) from RuntimeError(
                type(cause).__name__
            )

        # Only a 2xx means Rule B accepted the body. Redirects are failures here: these
        # clients do not follow them, so a 3xx leaves the payload undelivered.
        # The remote body is untrusted and may echo the requested webhook URI, so
        # logs and audit records keep the status code only.
        if not response.is_success:
            failure = f"Automation callback failed with HTTP {response.status_code}"
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
        # Incoming-webhook POSTs are not idempotent: Atlassian may accept the body and
        # start Rule B before we see a response. Retrying on timeout or 5xx can post a
        # second comment. A failed attempt stays a delivery failure; the scan JQL retries
        # later with a new run_id.
        if self._client is not None:
            return request_with_retries(
                self._client,
                "POST",
                url,
                max_retries=0,
                log_url=redacted_webhook_url(url),
                headers=headers,
                json=payload,
            )
        timeout = httpx.Timeout(self._settings.jira_automation_webhook_timeout_seconds)
        with httpx.Client(timeout=timeout) as client:
            return request_with_retries(
                client,
                "POST",
                url,
                max_retries=0,
                log_url=redacted_webhook_url(url),
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
