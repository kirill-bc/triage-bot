"""Search Jira issues by JQL (Cloud REST ``/rest/api/3/search/jql``)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from triage_service.adapters.jira_issue_fetcher import JiraIssueFetchError, _gateway_prefix
from triage_service.core.settings import AppSettings

_SEARCH_FIELDS = "issuetype,priority"
_DEFAULT_PAGE_SIZE = 50
_JIRA_MAX_RESULTS_CAP = 100


@dataclass(frozen=True)
class JiraSearchIssueRef:
    """Issue key plus current type/priority from JQL search."""

    issue_key: str
    issue_type: str
    priority: str | None


class JiraJqlSearchError(RuntimeError):
    """Raised when JQL search cannot complete."""


def _auth_headers(settings: AppSettings) -> dict[str, str]:
    email = settings.jira_user_email
    token = settings.jira_api_key
    if email is None or not str(email).strip() or token is None or not str(token).strip():
        raise JiraJqlSearchError(
            "Jira credentials required for JQL search (JIRA_USER_EMAIL, JIRA_API_KEY).",
        )
    from triage_service.adapters.jira_issue_fetcher import _basic_auth_header

    return {
        "Authorization": _basic_auth_header(str(email).strip(), str(token).strip()),
        "Accept": "application/json",
    }


def _parse_issue_ref(issue: dict[str, Any]) -> JiraSearchIssueRef | None:
    key = str(issue.get("key") or "").strip()
    if not key:
        return None
    fields = issue.get("fields") or {}
    issue_type_obj = fields.get("issuetype") or {}
    issue_type = str(issue_type_obj.get("name") or "").strip()
    priority_obj = fields.get("priority")
    if priority_obj is None:
        priority_name: str | None = None
    else:
        priority_name = str(priority_obj.get("name") or "").strip() or None
    return JiraSearchIssueRef(
        issue_key=key,
        issue_type=issue_type,
        priority=priority_name,
    )


def _fetch_search_page(
    client: httpx.Client,
    *,
    search_url: str,
    jql: str,
    next_page_token: str | None,
    headers: dict[str, str],
    page_max_results: int,
) -> tuple[list[dict[str, Any]], str | None]:
    params: dict[str, str | int] = {
        "jql": jql,
        "maxResults": page_max_results,
        "fields": _SEARCH_FIELDS,
    }
    if next_page_token:
        params["nextPageToken"] = next_page_token
    response = client.get(search_url, params=params, headers=headers)
    if response.status_code >= 400:
        raise JiraJqlSearchError(
            f"Jira JQL search failed with HTTP {response.status_code}: {response.text[:500]}",
        )
    data = response.json()
    issues_raw = data.get("issues")
    issues: list[dict[str, Any]] = issues_raw if isinstance(issues_raw, list) else []
    token = data.get("nextPageToken")
    next_out: str | None = token if isinstance(token, str) and token else None
    return issues, next_out


def _validate_jql_search_inputs(
    jql: str,
    max_results: int,
    jql_page_size: int | None,
) -> tuple[str, int]:
    stripped = jql.strip()
    if not stripped:
        raise JiraJqlSearchError("JQL must be a non-empty string.")
    if max_results < 1:
        raise JiraJqlSearchError("max_results must be at least 1.")
    page_cap = jql_page_size if jql_page_size is not None else _DEFAULT_PAGE_SIZE
    if page_cap < 1:
        raise JiraJqlSearchError("jql_page_size must be at least 1.")
    if page_cap > _JIRA_MAX_RESULTS_CAP:
        raise JiraJqlSearchError(
            f"jql_page_size must be at most {_JIRA_MAX_RESULTS_CAP} (Jira API cap).",
        )
    return stripped, page_cap


def _collect_issues_by_jql(
    http_client: httpx.Client,
    *,
    search_url: str,
    jql: str,
    headers: dict[str, str],
    max_results: int,
    page_cap: int,
) -> list[JiraSearchIssueRef]:
    out: list[JiraSearchIssueRef] = []
    next_token: str | None = None
    while len(out) < max_results:
        remaining = max_results - len(out)
        page_size = min(page_cap, remaining)
        issues, next_token = _fetch_search_page(
            http_client,
            search_url=search_url,
            jql=jql,
            next_page_token=next_token,
            headers=headers,
            page_max_results=page_size,
        )
        for issue in issues:
            ref = _parse_issue_ref(issue)
            if ref is not None:
                out.append(ref)
            if len(out) >= max_results:
                break
        if not next_token or not issues:
            break
    return out


def search_issues_by_jql(
    settings: AppSettings,
    jql: str,
    *,
    max_results: int,
    jql_page_size: int | None = None,
    client: httpx.Client | None = None,
) -> list[JiraSearchIssueRef]:
    """Return up to ``max_results`` issues matching ``jql`` (order is JQL-defined)."""
    stripped, page_cap = _validate_jql_search_inputs(jql, max_results, jql_page_size)
    try:
        prefix = _gateway_prefix(settings)
    except JiraIssueFetchError as exc:
        raise JiraJqlSearchError(str(exc)) from exc

    search_url = f"{prefix}/rest/api/3/search/jql"
    headers = _auth_headers(settings)
    if client is not None:
        return _collect_issues_by_jql(
            client,
            search_url=search_url,
            jql=stripped,
            headers=headers,
            max_results=max_results,
            page_cap=page_cap,
        )
    timeout = httpx.Timeout(settings.jira_http_timeout_seconds)
    with httpx.Client(timeout=timeout) as http_client:
        return _collect_issues_by_jql(
            http_client,
            search_url=search_url,
            jql=stripped,
            headers=headers,
            max_results=max_results,
            page_cap=page_cap,
        )
