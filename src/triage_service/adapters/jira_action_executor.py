"""Apply triage outcomes to Jira (labels, mismatch comments) via REST API v3."""

from __future__ import annotations

import base64
from typing import Any

import httpx

from triage_service.core.settings import AppSettings
from triage_service.adapters.jira_issue_fetcher import FetchedIssue
from triage_service.core.triage_fallback import TriageFailure
from triage_service.core.triage_mismatch import compute_mismatch_flags
from triage_service.core.triage_recommendation_parser import TriageRecommendation

# Display name for Jira mismatch comments (keep consistent with operator-facing bot naming).
_TRIAGEBOT_NAME = "TriageBot"
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


def _labels_for_outcome(recommendation: TriageRecommendation, issue: FetchedIssue) -> list[str]:
    flags = compute_mismatch_flags(issue, recommendation)
    labels = ["ai-reviewed"]
    if flags.type_mismatch and recommendation.recommended_issue_type == "Story":
        labels.append("ai-likely-story")
    if flags.priority_mismatch:
        labels.append("ai-priority-mismatch")
    return labels


def _mention_attrs(issue: FetchedIssue) -> dict[str, str]:
    aid = issue.reporter_account_id or ""
    display = issue.reporter.strip() if issue.reporter.strip() else aid
    text = f"@{display}" if display else "@Reporter"
    return {"id": aid, "text": text, "accessLevel": ""}


_MENTION_INTRO = (
    f" — {_TRIAGEBOT_NAME} (automated triage). Informational only; nothing was changed in Jira."
)

_NO_MENTION_INTRO = (
    f"{_TRIAGEBOT_NAME} (automated triage). Informational only; nothing was changed in Jira."
)


def _opening_paragraph_nodes(issue: FetchedIssue) -> list[dict[str, Any]]:
    if issue.reporter_account_id:
        return [
            {"type": "mention", "attrs": _mention_attrs(issue)},
            {"type": "text", "text": _MENTION_INTRO},
        ]
    return [{"type": "text", "text": _NO_MENTION_INTRO}]


def _suggestion_paragraph_text(issue: FetchedIssue, recommendation: TriageRecommendation) -> str:
    if recommendation.recommended_issue_type == "Story":
        return (
            "Suggested action: classify as Story. From the bug definition we apply, this reads as "
            "product or enablement work rather than a defect."
        )
    pri = recommendation.recommended_priority
    assert pri is not None  # Bug path: schema requires a P0–P4 priority
    raw = issue.priority
    if raw is None or not str(raw).strip():
        from_label = "(not set)"
    else:
        from_label = str(raw).strip()
    to_label = str(pri).strip()
    return (
        f"Suggested action: change priority: {from_label} -> {to_label}."
    )


def _rationale_paragraph_text(recommendation: TriageRecommendation) -> str:
    return f"Rationale: {recommendation.reason}"


def _mismatch_comment_body(
    issue: FetchedIssue,
    recommendation: TriageRecommendation,
) -> dict[str, Any]:
    return {
        "version": 1,
        "type": "doc",
        "content": [
            {"type": "paragraph", "content": _opening_paragraph_nodes(issue)},
            {
                "type": "paragraph",
                "content": [
                    {
                        "type": "text",
                        "text": _suggestion_paragraph_text(issue, recommendation),
                    },
                ],
            },
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": _rationale_paragraph_text(recommendation)}],
            },
        ],
    }


def _raise_for_status(response: httpx.Response, action: str) -> None:
    if response.is_error:
        snippet = response.text[:300]
        msg = f"Jira {action} failed with HTTP {response.status_code}: {snippet}"
        raise JiraActionExecutorError(msg)


class JiraTriageActionExecutor:
    """Apply ``ai-reviewed`` on success; on mismatch, labels plus a templated ADF comment.

    Copy uses a **TriageBot** internal-comment template: factual and easy to work with (support
    tone, not warnings or flattery). Numeric confidence stays out of Jira. When
    ``reporter_account_id`` is set, the comment opens with an ADF ``mention`` of the
    reporter (Jira REST v3).
    """

    def __init__(self, settings: AppSettings, *, client: httpx.Client | None = None) -> None:
        self._settings = settings
        self._client = client

    def apply_triage_outcome(
        self,
        *,
        issue: FetchedIssue | None,
        issue_key: str,
        project: str,
        source: str,
        outcome: TriageRecommendation | TriageFailure,
    ) -> None:
        _ = (project, source)
        if isinstance(outcome, TriageFailure):
            return
        if issue is None:
            return
        flags = compute_mismatch_flags(issue, outcome)
        labels = _labels_for_outcome(outcome, issue)
        base_url, headers = _jira_base_and_headers(self._settings)
        self._apply_labels(base_url, issue_key, labels, headers)
        if flags.any_mismatch():
            self._post_comment(base_url, issue_key, issue, outcome, headers)

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
            resp = self._client.put(url, json=body, headers=headers)
            _raise_for_status(resp, "issue label update")
            return
        with httpx.Client(timeout=30.0) as client:
            resp = client.put(url, json=body, headers=headers)
            _raise_for_status(resp, "issue label update")

    def _post_comment(
        self,
        base_url: str,
        issue_key: str,
        issue: FetchedIssue,
        recommendation: TriageRecommendation,
        headers: dict[str, str],
    ) -> None:
        url = f"{base_url}/rest/api/3/issue/{issue_key}/comment"
        payload = {"body": _mismatch_comment_body(issue, recommendation)}
        if self._client is not None:
            resp = self._client.post(url, json=payload, headers=headers)
            _raise_for_status(resp, "issue comment")
            return
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(url, json=payload, headers=headers)
            _raise_for_status(resp, "issue comment")
