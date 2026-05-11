"""Unit tests for synchronous per-issue triage orchestration."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from jira_issue_fetcher import FetchedIssue, JiraIssueFetcher
from openrouter_inference_client import OpenRouterInferenceClient
from policy_context import PolicyContext
from settings import AppSettings
from triage_fallback import TriageFailure
from triage_handler import TriageActionExecutor, TriageHandler
from triage_recommendation_parser import TriageRecommendation


class _RecordingExecutor(TriageActionExecutor):
    def __init__(self) -> None:
        self.calls: list[tuple[FetchedIssue | None, Any]] = []

    def apply_triage_outcome(
        self,
        *,
        issue: FetchedIssue | None,
        issue_key: str,
        project: str,
        source: str,
        outcome: TriageRecommendation | TriageFailure,
    ) -> None:
        self.calls.append((issue, outcome))


def _app_settings(monkeypatch: pytest.MonkeyPatch) -> AppSettings:
    monkeypatch.setenv("JIRA_API_KEY", "jira-api-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-token")
    monkeypatch.setenv("JIRA_BASE_URL", "https://example.atlassian.net")
    monkeypatch.setenv("JIRA_USER_EMAIL", "bot@example.com")
    return AppSettings()


def _policy() -> PolicyContext:
    return PolicyContext(bug_definition="bugs are defects", priority_definition="P0 is worst")


@pytest.mark.unit
def test_handler_story_path_calls_inference_once_and_returns_recommendation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _app_settings(monkeypatch)
    issue = FetchedIssue(
        issue_key="TJC-9",
        summary="s",
        description=None,
        issue_type="Bug",
        priority="Medium",
        reporter="r",
    )

    def jira_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_jira_payload_for(issue))

    story_json = '{"recommended_issue_type":"Story","confidence":0.7,"reason":"Narrative work."}'

    def openrouter_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": story_json}}]},
        )

    transport_j = httpx.MockTransport(jira_handler)
    transport_o = httpx.MockTransport(openrouter_handler)
    with httpx.Client(transport=transport_j) as j_client:
        with httpx.Client(transport=transport_o) as o_client:
            fetcher = JiraIssueFetcher(settings, client=j_client)
            inference = OpenRouterInferenceClient(settings, client=o_client)
            executor = _RecordingExecutor()
            handler = TriageHandler(
                allowed_projects=("TJC",),
                fetcher=fetcher,
                inference=inference,
                policy=_policy(),
                executor=executor,
            )
            outcome = handler.run_sync(
                issue_key="TJC-9",
                project="TJC",
                source="scheduled_scan",
            )

    assert isinstance(outcome, TriageRecommendation)
    assert outcome.recommended_issue_type == "Story"
    assert outcome.recommended_priority is None
    assert len(executor.calls) == 1
    applied_issue, applied_outcome = executor.calls[0]
    assert applied_issue == issue
    assert applied_outcome == outcome


@pytest.mark.unit
def test_handler_bug_path_calls_inference_twice_and_merges_priority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _app_settings(monkeypatch)
    issue = FetchedIssue(
        issue_key="TJC-10",
        summary="crash",
        description="segfault",
        issue_type="Bug",
        priority="Low",
        reporter="bob",
    )

    def jira_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_jira_payload_for(issue))

    cls_json = '{"recommended_issue_type":"Bug","confidence":0.55,"reason":"Defect."}'
    pri_json = '{"recommended_priority":"P1","confidence":0.88,"reason":"Data loss risk."}'
    responses = [cls_json, pri_json]
    idx = {"i": 0}

    def openrouter_handler(request: httpx.Request) -> httpx.Response:
        i = idx["i"]
        idx["i"] = i + 1
        content = responses[i]
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": content}}]},
        )

    transport_j = httpx.MockTransport(jira_handler)
    transport_o = httpx.MockTransport(openrouter_handler)
    with httpx.Client(transport=transport_j) as j_client:
        with httpx.Client(transport=transport_o) as o_client:
            fetcher = JiraIssueFetcher(settings, client=j_client)
            inference = OpenRouterInferenceClient(settings, client=o_client)
            executor = _RecordingExecutor()
            handler = TriageHandler(
                allowed_projects=("TJC",),
                fetcher=fetcher,
                inference=inference,
                policy=_policy(),
                executor=executor,
            )
            outcome = handler.run_sync(
                issue_key="TJC-10",
                project="TJC",
                source="scheduled_scan",
            )

    assert isinstance(outcome, TriageRecommendation)
    assert outcome.recommended_issue_type == "Bug"
    assert outcome.recommended_priority == "P1"
    assert outcome.confidence == 0.88
    assert outcome.reason == "Data loss risk."
    assert idx["i"] == 2


@pytest.mark.unit
def test_handler_rejects_project_not_in_allowlist_without_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _app_settings(monkeypatch)

    def jira_handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("Jira should not be called when project is rejected")

    transport_j = httpx.MockTransport(jira_handler)
    with httpx.Client(transport=transport_j) as j_client:
        fetcher = JiraIssueFetcher(settings, client=j_client)
        or_transport = httpx.MockTransport(_fail)
        with httpx.Client(transport=or_transport) as o_client:
            inference = OpenRouterInferenceClient(settings, client=o_client)
            executor = _RecordingExecutor()
            handler = TriageHandler(
                allowed_projects=("TJC",),
                fetcher=fetcher,
                inference=inference,
                policy=_policy(),
                executor=executor,
            )
            outcome = handler.run_sync(
                issue_key="XX-1",
                project="XX",
                source="scheduled_scan",
            )

    assert isinstance(outcome, TriageFailure)
    assert outcome.category == "project_not_allowed"
    assert len(executor.calls) == 1
    assert executor.calls[0][0] is None
    assert executor.calls[0][1] == outcome


def _fail(request: httpx.Request) -> httpx.Response:
    raise AssertionError("OpenRouter should not run")


@pytest.mark.unit
def test_handler_passes_triage_failure_to_executor_on_jira_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _app_settings(monkeypatch)

    def jira_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="unavailable")

    transport_j = httpx.MockTransport(jira_handler)
    with httpx.Client(transport=transport_j) as j_client:
        fetcher = JiraIssueFetcher(settings, client=j_client)
        or_transport = httpx.MockTransport(_fail)
        with httpx.Client(transport=or_transport) as o_client:
            inference = OpenRouterInferenceClient(settings, client=o_client)
            executor = _RecordingExecutor()
            handler = TriageHandler(
                allowed_projects=("TJC",),
                fetcher=fetcher,
                inference=inference,
                policy=_policy(),
                executor=executor,
            )
            outcome = handler.run_sync(
                issue_key="TJC-1",
                project="TJC",
                source="scheduled_scan",
            )

    assert isinstance(outcome, TriageFailure)
    assert outcome.category == "jira_fetch_failed"
    assert executor.calls[0][0] is None
    assert executor.calls[0][1] == outcome


def _jira_payload_for(issue: FetchedIssue) -> dict[str, Any]:
    desc: str | dict[str, Any] | None
    if issue.description is None:
        desc = None
    else:
        desc = issue.description
    return {
        "key": issue.issue_key,
        "fields": {
            "summary": issue.summary,
            "description": desc,
            "issuetype": {"name": issue.issue_type},
            "priority": ({"name": issue.priority} if issue.priority else None),
            "reporter": {"displayName": issue.reporter},
        },
    }
