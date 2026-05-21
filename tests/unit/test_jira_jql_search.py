"""Jira JQL search adapter."""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from triage_service.adapters.jira_jql_search import (
    JiraJqlSearchError,
    search_issues_by_jql,
)
from triage_service.core.settings import AppSettings


def _settings() -> AppSettings:
    return AppSettings.model_validate(
        {
            "jira_cloud_id": "cloud-1",
            "jira_user_email": "user@example.com",
            "jira_api_key": "token",
            "openrouter_api_key": "or-key",
            "TRIAGE_WEBHOOK_TOKEN": "wh",
            "triage_allowed_projects": "TJC",
        },
    )


@pytest.mark.unit
def test_search_issues_by_jql_parses_keys_and_current_fields() -> None:
    client = MagicMock(spec=httpx.Client)
    client.get.return_value = httpx.Response(
        200,
        json={
            "issues": [
                {
                    "key": "TJC-1",
                    "fields": {
                        "issuetype": {"name": "Bug"},
                        "priority": {"name": "P2"},
                    },
                },
                {
                    "key": "TJC-2",
                    "fields": {
                        "issuetype": {"name": "Story"},
                        "priority": None,
                    },
                },
            ],
            "nextPageToken": None,
        },
    )
    refs = search_issues_by_jql(
        _settings(),
        "project = TJC",
        max_results=10,
        client=client,
    )
    assert len(refs) == 2
    assert refs[0].issue_key == "TJC-1"
    assert refs[0].issue_type == "Bug"
    assert refs[0].priority == "P2"
    assert refs[1].issue_key == "TJC-2"
    assert refs[1].issue_type == "Story"
    assert refs[1].priority is None


@pytest.mark.unit
def test_search_issues_by_jql_respects_limit_across_pages() -> None:
    client = MagicMock(spec=httpx.Client)
    client.get.side_effect = [
        httpx.Response(
            200,
            json={
                "issues": [{"key": "TJC-0", "fields": {"issuetype": {"name": "Bug"}}}],
                "nextPageToken": "page-2",
            },
        ),
        httpx.Response(
            200,
            json={
                "issues": [{"key": "TJC-99", "fields": {"issuetype": {"name": "Bug"}}}],
                "nextPageToken": None,
            },
        ),
    ]
    refs = search_issues_by_jql(_settings(), "project = TJC", max_results=1, client=client)
    assert client.get.call_count == 1
    assert client.get.call_args.kwargs["params"]["maxResults"] == 1
    assert len(refs) == 1
    assert refs[0].issue_key == "TJC-0"


@pytest.mark.unit
def test_search_issues_by_jql_uses_jql_page_size_on_first_request() -> None:
    client = MagicMock(spec=httpx.Client)
    client.get.return_value = httpx.Response(
        200,
        json={"issues": [], "nextPageToken": None},
    )
    search_issues_by_jql(
        _settings(),
        "project = TJC",
        max_results=200,
        jql_page_size=100,
        client=client,
    )
    assert client.get.call_args.kwargs["params"]["maxResults"] == 100


@pytest.mark.unit
def test_search_issues_by_jql_rejects_page_size_above_jira_cap() -> None:
    with pytest.raises(JiraJqlSearchError, match="at most 100"):
        search_issues_by_jql(_settings(), "project = TJC", max_results=5, jql_page_size=101)


@pytest.mark.unit
def test_search_issues_by_jql_rejects_empty_jql() -> None:
    with pytest.raises(JiraJqlSearchError, match="non-empty"):
        search_issues_by_jql(_settings(), "   ", max_results=5)
