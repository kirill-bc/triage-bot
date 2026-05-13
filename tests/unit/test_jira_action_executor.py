"""Unit tests for Jira label and mismatch-comment application."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from jira_action_executor import JiraActionExecutorError, JiraTriageActionExecutor
from jira_issue_fetcher import FetchedIssue
from settings import AppSettings
from triage_fallback import TriageFailure
from triage_recommendation_parser import TriageRecommendation


def _settings(monkeypatch: pytest.MonkeyPatch) -> AppSettings:
    monkeypatch.setenv("JIRA_API_KEY", "jira-api-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-token")
    monkeypatch.setenv("JIRA_BASE_URL", "https://example.atlassian.net")
    monkeypatch.setenv("JIRA_USER_EMAIL", "bot@example.com")
    return AppSettings()


def _issue(**overrides: Any) -> FetchedIssue:
    base: dict[str, Any] = {
        "issue_key": "TJC-1",
        "summary": "s",
        "description": None,
        "issue_type": "Bug",
        "priority": "P2",
        "reporter": "Alice",
    }
    base.update(overrides)
    return FetchedIssue.model_validate(base)


def _rec(**overrides: Any) -> TriageRecommendation:
    base: dict[str, Any] = {
        "recommended_issue_type": "Bug",
        "recommended_priority": "P2",
        "confidence": 0.5,
        "reason": "aligned",
    }
    base.update(overrides)
    return TriageRecommendation.model_validate(base)


@pytest.mark.unit
def test_executor_no_http_on_triage_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method + " " + str(request.url.path))
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        ex = JiraTriageActionExecutor(settings, client=client)
        ex.apply_triage_outcome(
            issue=_issue(),
            issue_key="TJC-1",
            project="TJC",
            source="bug_created",
            outcome=TriageFailure(category="inference_failed", message="down"),
        )
    assert calls == []


@pytest.mark.unit
def test_executor_applies_only_ai_reviewed_when_no_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(monkeypatch)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204)

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        ex = JiraTriageActionExecutor(settings, client=client)
        ex.apply_triage_outcome(
            issue=_issue(),
            issue_key="TJC-1",
            project="TJC",
            source="bug_created",
            outcome=_rec(),
        )

    assert len(requests) == 1
    assert requests[0].method == "PUT"
    assert requests[0].url.path == "/rest/api/3/issue/TJC-1"
    body = json.loads(requests[0].content.decode())
    assert body == {"update": {"labels": [{"add": "ai-reviewed"}]}}
    assert "/comment" not in str(requests[0].url)


@pytest.mark.unit
def test_executor_posts_mismatch_comment_with_reporter_mention(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(monkeypatch)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(201, json={"id": "c1"})

    transport = httpx.MockTransport(handler)
    issue = _issue(
        issue_type="Bug",
        priority="P1",
        reporter="Juan Estrada",
        reporter_account_id="61d4a5c6e67ea2006bce3aaa",
    )
    with httpx.Client(transport=transport) as client:
        ex = JiraTriageActionExecutor(settings, client=client)
        ex.apply_triage_outcome(
            issue=issue,
            issue_key="TJC-2",
            project="TJC",
            source="bug_created",
            outcome=_rec(recommended_priority="P2", reason="severity"),
        )

    assert len(requests) == 2
    assert requests[0].method == "PUT"
    put_body = json.loads(requests[0].content.decode())
    adds = [x["add"] for x in put_body["update"]["labels"]]
    assert adds == ["ai-reviewed", "ai-priority-mismatch"]

    assert requests[1].method == "POST"
    assert requests[1].url.path == "/rest/api/3/issue/TJC-2/comment"
    comment_body = json.loads(requests[1].content.decode())
    raw = requests[1].content.decode()
    assert "Confidence" not in raw
    paras = comment_body["body"]["content"]
    assert len(paras) == 3
    first_para = paras[0]["content"]
    mention = first_para[0]
    assert mention["type"] == "mention"
    assert mention["attrs"]["id"] == "61d4a5c6e67ea2006bce3aaa"
    assert mention["attrs"]["text"] == "@Juan Estrada"
    assert "accessLevel" in mention["attrs"]
    intro = first_para[1]["text"].lower()
    assert "triagebot" in intro
    assert "automated triage" in intro
    assert "informational only" in intro
    assert "nothing was changed in jira" in intro
    mid = paras[1]["content"][0]["text"].lower()
    assert "suggested action: change priority:" in mid
    assert "p1 -> p2" in mid
    assert paras[2]["content"][0]["text"] == "Rationale: severity"


@pytest.mark.unit
def test_executor_mismatch_comment_without_account_id_has_no_mention_node(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(monkeypatch)
    posted: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            posted.append(json.loads(request.content.decode()))
        return httpx.Response(201 if request.method == "POST" else 204)

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        ex = JiraTriageActionExecutor(settings, client=client)
        ex.apply_triage_outcome(
            issue=_issue(
                issue_type="Bug",
                priority="P1",
                reporter="Bob",
                reporter_account_id=None,
            ),
            issue_key="TJC-3",
            project="TJC",
            source="manual_cli",
            outcome=_rec(recommended_priority="P3", reason="defect"),
        )

    doc = posted[0]["body"]
    first_para_nodes = doc["content"][0]["content"]
    assert all(n["type"] == "text" for n in first_para_nodes)
    assert "triagebot" in first_para_nodes[0]["text"].lower()
    mid = doc["content"][1]["content"][0]["text"].lower()
    assert "suggested action: change priority:" in mid
    assert "p1 -> p3" in mid
    assert doc["content"][2]["content"][0]["text"] == "Rationale: defect"


@pytest.mark.unit
def test_executor_story_mismatch_uses_story_suggestion_template(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(monkeypatch)
    posted: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            posted.append(json.loads(request.content.decode()))
        return httpx.Response(201 if request.method == "POST" else 204)

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        ex = JiraTriageActionExecutor(settings, client=client)
        ex.apply_triage_outcome(
            issue=_issue(issue_type="Bug"),
            issue_key="TJC-9",
            project="TJC",
            source="bug_created",
            outcome=_rec(
                recommended_issue_type="Story",
                recommended_priority=None,
                reason="User story framing.",
            ),
        )

    mid = posted[0]["body"]["content"][1]["content"][0]["text"]
    assert "Suggested action: classify as Story" in mid
    assert "priority" not in mid.lower()
    assert posted[0]["body"]["content"][2]["content"][0]["text"] == (
        "Rationale: User story framing."
    )


@pytest.mark.unit
def test_executor_raises_when_jira_rest_target_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("JIRA_CLOUD_ID", raising=False)
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.setenv("JIRA_API_KEY", "jira-api-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-token")
    monkeypatch.setenv("JIRA_USER_EMAIL", "bot@example.com")
    settings = AppSettings()

    transport = httpx.MockTransport(lambda r: httpx.Response(200))
    with httpx.Client(transport=transport) as client:
        ex = JiraTriageActionExecutor(settings, client=client)
        with pytest.raises(JiraActionExecutorError) as exc:
            ex.apply_triage_outcome(
                issue=_issue(),
                issue_key="TJC-1",
                project="TJC",
                source="bug_created",
                outcome=_rec(),
            )
    msg = str(exc.value).lower()
    assert "jira_cloud_id" in msg or "cloud_id" in msg
    assert "jira_base_url" in msg or "base_url" in msg


@pytest.mark.unit
def test_executor_uses_atlassian_gateway_when_jira_cloud_id_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JIRA_API_KEY", "jira-api-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-token")
    monkeypatch.setenv("JIRA_CLOUD_ID", "550e8400-e29b-41d4-a716-446655440000")
    monkeypatch.setenv("JIRA_USER_EMAIL", "bot@example.com")
    settings = AppSettings()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204 if request.method == "PUT" else 201, json={})

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        ex = JiraTriageActionExecutor(settings, client=client)
        ex.apply_triage_outcome(
            issue=_issue(),
            issue_key="TJC-1",
            project="TJC",
            source="bug_created",
            outcome=_rec(),
        )

    assert len(requests) == 1
    assert requests[0].url.host == "api.atlassian.com"
    assert requests[0].url.path == (
        "/ex/jira/550e8400-e29b-41d4-a716-446655440000/rest/api/3/issue/TJC-1"
    )


@pytest.mark.unit
def test_executor_raises_on_label_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="bad label")

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        ex = JiraTriageActionExecutor(settings, client=client)
        with pytest.raises(JiraActionExecutorError, match="400"):
            ex.apply_triage_outcome(
                issue=_issue(),
                issue_key="TJC-1",
                project="TJC",
                source="bug_created",
                outcome=_rec(),
            )
