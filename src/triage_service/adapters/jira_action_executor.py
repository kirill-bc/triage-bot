"""Apply triage outcomes to Jira (labels, mismatch comments) via REST API v3."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import httpx

from triage_service.adapters.jira_http_retry import (
    TransportRetriesExhausted,
    request_with_retries,
)
from triage_service.core.settings import AppSettings
from triage_service.adapters.jira_issue_fetcher import FetchedIssue
from triage_service.core.triage_fallback import TriageFailure
from triage_service.core.triage_mismatch import compute_mismatch_flags
from triage_service.core.triage_recommendation_parser import TriageRecommendation

# Display name for Jira mismatch comments (keep consistent with operator-facing bot naming).
_TRIAGEBOT_NAME = "TriageBot"
_ATLASSIAN_GATEWAY = "https://api.atlassian.com/ex/jira"
_COMMENT_TEMPLATES_PATH = Path(__file__).resolve().parent / "jira_comment_templates.json"


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
    labels = ["triagebot-reviewed"]
    if flags.type_mismatch and recommendation.recommended_issue_type == "Story":
        labels.append("triagebot-likely-story")
    if _priority_signal(issue, recommendation) in ("prioritize", "deescalate"):
        labels.append("triagebot-priority-mismatch")
    return labels


def _load_comment_templates() -> dict[str, dict[str, str]]:
    raw = json.loads(_COMMENT_TEMPLATES_PATH.read_text(encoding="utf-8"))
    advisory = raw["advisory"]
    applied = raw["applied"]
    return {
        "advisory": {k: str(v) for k, v in advisory.items()},
        "applied": {k: str(v) for k, v in applied.items()},
    }


_COMMENT_TEMPLATES = _load_comment_templates()


def _mention_attrs(issue: FetchedIssue) -> dict[str, str]:
    aid = issue.reporter_account_id or ""
    display = issue.reporter.strip() if issue.reporter.strip() else aid
    text = f"@{display}" if display else "@Reporter"
    return {"id": aid, "text": text, "accessLevel": ""}


def _opening_paragraph_nodes(
    issue: FetchedIssue,
    *,
    mutations_applied: bool,
) -> list[dict[str, Any]]:
    template_group = _COMMENT_TEMPLATES["applied" if mutations_applied else "advisory"]
    mention_intro = template_group["mention_intro"]
    no_mention_intro = template_group["no_mention_intro"]
    if issue.reporter_account_id:
        return [
            {"type": "mention", "attrs": _mention_attrs(issue)},
            {"type": "text", "text": mention_intro},
        ]
    return [{"type": "text", "text": no_mention_intro}]


def _suggestion_paragraph_text(
    issue: FetchedIssue,
    recommendation: TriageRecommendation,
    *,
    mutations_applied: bool,
) -> str:
    template_group = _COMMENT_TEMPLATES["applied" if mutations_applied else "advisory"]
    if recommendation.recommended_issue_type == "Story":
        body = template_group["story_action"]
        return f"- {body}" if mutations_applied else body
    pri = recommendation.recommended_priority
    assert pri is not None  # Bug path: schema requires a P0–P4 priority
    raw = issue.priority
    if raw is None or not str(raw).strip():
        from_label = "(not set)"
    else:
        from_label = str(raw).strip()
    to_label = str(pri).strip()
    body = template_group["priority_action"].format(
        from_priority=from_label,
        to_priority=to_label,
    )
    return f"- {body}" if mutations_applied else body


def _rationale_paragraph_text(
    recommendation: TriageRecommendation,
    *,
    mutations_applied: bool,
) -> str:
    template_group = _COMMENT_TEMPLATES["applied" if mutations_applied else "advisory"]
    return template_group["rationale"].format(reason=recommendation.reason)


def _p0_p4_rank(label: str | None) -> int | None:
    """Map P0..P4 to 0..4 for ordering (lower rank = more urgent). Unknown labels -> None."""
    if label is None:
        return None
    s = str(label).strip().upper()
    if len(s) != 2 or s[0] != "P" or s[1] not in "01234":
        return None
    return int(s[1])


def _closing_paragraph_text(
    issue: FetchedIssue,
    recommendation: TriageRecommendation,
    *,
    mutations_applied: bool,
) -> str:
    template_group = _COMMENT_TEMPLATES["applied" if mutations_applied else "advisory"]
    if recommendation.recommended_issue_type == "Story":
        return template_group["closing_bug"]
    raw = issue.priority
    if raw is None or not str(raw).strip():
        current = "(not set)"
    else:
        current = str(raw).strip()
    return template_group["closing_priority"].format(current_priority=current)


def _mismatch_comment_body(
    issue: FetchedIssue,
    recommendation: TriageRecommendation,
    *,
    mutations_applied: bool,
) -> dict[str, Any]:
    content: list[dict[str, Any]] = [
        {
            "type": "paragraph",
            "content": _opening_paragraph_nodes(issue, mutations_applied=mutations_applied),
        }
    ]
    content.append(
        {
            "type": "paragraph",
            "content": [
                {
                    "type": "text",
                    "text": _suggestion_paragraph_text(
                        issue,
                        recommendation,
                        mutations_applied=mutations_applied,
                    ),
                },
            ],
        },
    )

    content.append(
        {
            "type": "paragraph",
            "content": [
                {
                    "type": "text",
                    "text": _rationale_paragraph_text(
                        recommendation,
                        mutations_applied=mutations_applied,
                    ),
                },
            ],
        },
    )
    content.append(
        {
            "type": "paragraph",
            "content": [
                {
                    "type": "text",
                    "text": _closing_paragraph_text(
                        issue,
                        recommendation,
                        mutations_applied=mutations_applied,
                    ),
                },
            ],
        },
    )
    return {"version": 1, "type": "doc", "content": content}


def _raise_for_status(response: httpx.Response, action: str) -> None:
    if response.is_error:
        snippet = response.text[:300]
        msg = f"Jira {action} failed with HTTP {response.status_code}: {snippet}"
        raise JiraActionExecutorError(msg)


def _priority_signal(issue: FetchedIssue, recommendation: TriageRecommendation) -> str | None:
    """Return ``prioritize``/``deescalate`` when Bug priorities are comparable, else ``None``."""
    if recommendation.recommended_issue_type != "Bug":
        return None
    rec_pri = recommendation.recommended_priority
    if rec_pri is None:
        return None
    orig_rank = _p0_p4_rank(issue.priority)
    rec_rank = _p0_p4_rank(str(rec_pri))
    if orig_rank is None or rec_rank is None:
        return None
    if rec_rank < orig_rank:
        return "prioritize"
    if rec_rank > orig_rank:
        return "deescalate"
    return None


def _should_post_mismatch_comment(
    *,
    issue: FetchedIssue,
    recommendation: TriageRecommendation,
) -> bool:
    """Comment for likely-story and all priority mismatches (prioritize + de-escalate)."""
    flags = compute_mismatch_flags(issue, recommendation)
    if flags.type_mismatch and recommendation.recommended_issue_type == "Story":
        return True
    signal = _priority_signal(issue, recommendation)
    return signal in ("prioritize", "deescalate")


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
    ) -> None:
        _ = (project, source, run_id)
        if isinstance(outcome, TriageFailure):
            return
        if issue is None:
            return
        labels = _labels_for_outcome(outcome, issue)
        base_url, headers = _jira_base_and_headers(self._settings)
        self._apply_labels(base_url, issue_key, labels, headers)
        should_post_comment = _should_post_mismatch_comment(
            issue=issue,
            recommendation=outcome,
        )
        mutations_applied = False
        mutation_error: JiraActionExecutorError | None = None
        try:
            mutations_applied = self._maybe_apply_recommended_mutations(
                base_url=base_url,
                issue_key=issue_key,
                issue=issue,
                recommendation=outcome,
                headers=headers,
            )
        except JiraActionExecutorError as exc:
            mutation_error = exc
            mutations_applied = False
        if self._post_mismatch_comments and should_post_comment:
            self._post_comment(
                base_url,
                issue_key,
                issue,
                outcome,
                headers,
                mutations_applied=mutations_applied,
            )
            return
        if mutation_error is not None:
            raise mutation_error

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
        issue: FetchedIssue,
        recommendation: TriageRecommendation,
        headers: dict[str, str],
    ) -> bool:
        flags = compute_mismatch_flags(issue, recommendation)
        applied = False
        if (
            self._auto_apply_bug_to_story
            and flags.type_mismatch
            and str(issue.issue_type).strip().upper() == "BUG"
            and recommendation.recommended_issue_type == "Story"
        ):
            self._update_issue_fields(
                base_url,
                issue_key,
                {"issuetype": {"name": "Story"}},
                headers,
            )
            applied = True
        if (
            self._auto_apply_deescalation
            and flags.priority_mismatch
            and _priority_signal(issue, recommendation) == "deescalate"
        ):
            rec_priority = recommendation.recommended_priority
            if rec_priority is not None:
                self._update_issue_fields(
                    base_url,
                    issue_key,
                    {"priority": {"name": str(rec_priority).strip()}},
                    headers,
                )
                applied = True
        return applied

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
