"""Apply triage outcomes to Jira (labels, mismatch comments) via REST API v3."""

from __future__ import annotations

import base64
from typing import Any

import httpx

from triage_service.adapters.jira_issue_fetcher import FetchedIssue
from triage_service.adapters.jira_http_retry import (
    TransportRetriesExhausted,
    request_with_retries,
)
from triage_service.adapters.triage_outcome_rendering import (
    AutoApplyPolicy,
    OutcomeDecision,
    build_outcome_decision,
    render_adf_comment,
)
from triage_service.core.settings import AppSettings
from triage_service.core.triage_action_applied import TriageActionAppliedFlags
from triage_service.core.triage_fallback import TriageFailure
from triage_service.core.triage_recommendation_parser import TriageRecommendation

_ATLASSIAN_GATEWAY = "https://api.atlassian.com/ex/jira"


class JiraActionExecutorError(RuntimeError):
    """Raised when a Jira REST call to apply labels or a comment fails."""


def _basic_auth_header(email: str, api_token: str) -> str:
    token_bytes = f"{email}:{api_token}".encode("utf-8")
    encoded = base64.b64encode(token_bytes).decode("ascii")
    return f"Basic {encoded}"


def _jira_base_and_headers(settings: AppSettings) -> tuple[str, dict[str, str]]:
    cloud_raw = settings.jira_cloud_id
    if cloud_raw is None or not str(cloud_raw).strip():
        msg = "Jira REST requires JIRA_CLOUD_ID (Atlassian gateway)."
        raise JiraActionExecutorError(msg)
    cloud_id = str(cloud_raw).strip()
    prefix = f"{_ATLASSIAN_GATEWAY}/{cloud_id}"
    email = settings.jira_user_email
    if email is None or not str(email).strip():
        msg = "Jira user email is required for REST auth (set JIRA_USER_EMAIL)."
        raise JiraActionExecutorError(msg)
    headers = {
        "Authorization": _basic_auth_header(email, settings.jira_api_key),
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    return prefix, headers


def _mismatch_comment_body(
    issue: FetchedIssue,
    recommendation: TriageRecommendation,
    *,
    mutations_applied: bool,
) -> dict[str, Any]:
    """Compatibility wrapper for callers of the former private renderer."""
    return render_adf_comment(
        issue,
        recommendation,
        mutations_applied=mutations_applied,
    )


def _raise_for_status(response: httpx.Response, action: str) -> None:
    if response.is_error:
        snippet = response.text[:300]
        msg = f"Jira {action} failed with HTTP {response.status_code}: {snippet}"
        raise JiraActionExecutorError(msg)


def _should_post_mismatch_comment(
    *,
    issue: FetchedIssue,
    recommendation: TriageRecommendation,
) -> bool:
    """Compatibility wrapper for callers of the former private decision helper."""
    return build_outcome_decision(
        issue,
        recommendation,
        policy=AutoApplyPolicy(),
    ).post_comment


class JiraTriageActionExecutor:
    """Apply ``triagebot-reviewed`` on success; on mismatch, labels plus a templated ADF comment.

    Copy uses a **TriageBot** internal-comment template: factual and easy to work with (support
    tone, not warnings or flattery). Numeric confidence stays out of Jira. When
    ``reporter_account_id`` is set, the comment opens with an ADF ``mention`` of the
    reporter (Jira REST v3).
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
    ) -> None:
        self._settings = settings
        self._client = client
        self._post_mismatch_comments = post_mismatch_comments
        self._auto_apply_deescalation = (
            settings.triage_auto_apply_deescalation
            if auto_apply_deescalation is None
            else auto_apply_deescalation
        )
        self._auto_apply_escalation = (
            settings.triage_auto_apply_escalation
            if auto_apply_escalation is None
            else auto_apply_escalation
        )
        self._auto_apply_bug_to_story = (
            settings.triage_auto_apply_bug_to_story
            if auto_apply_bug_to_story is None
            else auto_apply_bug_to_story
        )

    def _request(
        self,
        client: httpx.Client,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any] | None = None,
    ) -> httpx.Response:
        kwargs: dict[str, Any] = {"headers": headers}
        if json is not None:
            kwargs["json"] = json
        try:
            response, _attempts = request_with_retries(
                client,
                method,
                url,
                max_retries=self._settings.jira_http_max_retries,
                **kwargs,
            )
        except (TransportRetriesExhausted, httpx.RequestError) as exc:
            msg = f"Jira request failed after retries: {exc}"
            raise JiraActionExecutorError(msg) from exc
        return response

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
        _ = (project, source, run_id)
        if isinstance(outcome, TriageFailure):
            return TriageActionAppliedFlags()
        if issue is None:
            return TriageActionAppliedFlags()
        decision = build_outcome_decision(
            issue,
            outcome,
            policy=AutoApplyPolicy(
                apply_deescalation=self._auto_apply_deescalation,
                apply_escalation=self._auto_apply_escalation,
                apply_bug_to_story=self._auto_apply_bug_to_story,
            ),
        )
        base_url, headers = _jira_base_and_headers(self._settings)
        self._apply_labels(base_url, issue_key, decision.labels, headers)
        applied = TriageActionAppliedFlags()
        mutation_error: JiraActionExecutorError | None = None
        try:
            applied = self._maybe_apply_recommended_mutations(
                base_url=base_url,
                issue_key=issue_key,
                decision=decision,
                headers=headers,
            )
        except JiraActionExecutorError as exc:
            mutation_error = exc
            applied = TriageActionAppliedFlags()
        if self._post_mismatch_comments and decision.post_comment:
            self._post_comment(
                base_url,
                issue_key,
                issue,
                outcome,
                headers,
                mutations_applied=(
                    applied.applied_type_change or applied.applied_priority_change
                ),
            )
            return applied
        if mutation_error is not None:
            raise mutation_error
        return applied

    def _apply_labels(
        self,
        base_url: str,
        issue_key: str,
        labels: list[str],
        headers: dict[str, str],
    ) -> None:
        url = f"{base_url}/rest/api/3/issue/{issue_key}"
        body = {"update": {"labels": [{"add": label} for label in labels]}}
        if self._client is not None:
            resp = self._request(self._client, "PUT", url, headers=headers, json=body)
            _raise_for_status(resp, "issue label update")
            return
        timeout = httpx.Timeout(self._settings.jira_http_timeout_seconds)
        with httpx.Client(timeout=timeout) as client:
            resp = self._request(client, "PUT", url, headers=headers, json=body)
            _raise_for_status(resp, "issue label update")

    def _post_comment(
        self,
        base_url: str,
        issue_key: str,
        issue: FetchedIssue,
        recommendation: TriageRecommendation,
        headers: dict[str, str],
        *,
        mutations_applied: bool,
    ) -> None:
        url = f"{base_url}/rest/api/3/issue/{issue_key}/comment"
        payload = {
            "body": _mismatch_comment_body(
                issue,
                recommendation,
                mutations_applied=mutations_applied,
            ),
        }
        if self._client is not None:
            resp = self._request(self._client, "POST", url, headers=headers, json=payload)
            _raise_for_status(resp, "issue comment")
            return
        timeout = httpx.Timeout(self._settings.jira_http_timeout_seconds)
        with httpx.Client(timeout=timeout) as client:
            resp = self._request(client, "POST", url, headers=headers, json=payload)
            _raise_for_status(resp, "issue comment")

    def _maybe_apply_recommended_mutations(
        self,
        *,
        base_url: str,
        issue_key: str,
        decision: OutcomeDecision,
        headers: dict[str, str],
    ) -> TriageActionAppliedFlags:
        applied_type_change = False
        applied_priority_change = False
        if decision.apply_bug_to_story:
            self._update_issue_fields(
                base_url,
                issue_key,
                {"issuetype": {"name": "Story"}},
                headers,
            )
            applied_type_change = True
        if decision.apply_priority is not None:
            self._update_issue_fields(
                base_url,
                issue_key,
                {"priority": {"name": decision.apply_priority.to_priority}},
                headers,
            )
            applied_priority_change = True
        return TriageActionAppliedFlags(
            applied_type_change=applied_type_change,
            applied_priority_change=applied_priority_change,
        )

    def _update_issue_fields(
        self,
        base_url: str,
        issue_key: str,
        fields: dict[str, Any],
        headers: dict[str, str],
    ) -> None:
        url = f"{base_url}/rest/api/3/issue/{issue_key}"
        body = {"fields": fields}
        if self._client is not None:
            resp = self._request(self._client, "PUT", url, headers=headers, json=body)
            _raise_for_status(resp, "issue field update")
            return
        timeout = httpx.Timeout(self._settings.jira_http_timeout_seconds)
        with httpx.Client(timeout=timeout) as client:
            resp = self._request(client, "PUT", url, headers=headers, json=body)
            _raise_for_status(resp, "issue field update")
