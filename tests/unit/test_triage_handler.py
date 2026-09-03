"""Unit tests for synchronous per-issue triage orchestration."""

from __future__ import annotations

from contextlib import contextmanager
import json
import logging
from typing import Any

import httpx
import pytest
from unittest.mock import MagicMock, patch

from triage_service.adapters.jira_issue_fetcher import (
    FetchedIssue,
    JiraIssueFetcher,
    LinkedZendeskTicket,
    ZendeskResolutionSummary,
)
from triage_service.adapters.openrouter_inference_client import OpenRouterInferenceClient
from triage_service.adapters.zendesk_comment_summarizer import (
    NoOpZendeskCommentSummarizer,
    ZendeskSummarizationResult,
)
from triage_service.adapters.zendesk_ticket_fetcher import (
    ZendeskTicketFetcher,
    ZendeskTicketsFetchResult,
)
from triage_service.core.settings import AppSettings
from triage_service.core.triage_fallback import TriageFailure
from triage_service.core.triage_handler import (
    TriageActionExecutor,
    TriageHandler,
    build_default_triage_handler,
)
from triage_service.core.policy_context import PolicyContext
from triage_service.core.triage_recommendation_parser import TriageRecommendation
from triage_service.observability.audit_events import (
    ClassificationCompletedAuditEvent,
    PriorityCompletedAuditEvent,
    TriageCompletedAuditEvent,
    TriageFailedAuditEvent,
)


class _RecordingExecutor(TriageActionExecutor):
    def __init__(self) -> None:
        self.calls: list[tuple[FetchedIssue | None, Any, str]] = []

    def apply_triage_outcome(
        self,
        *,
        issue: FetchedIssue | None,
        issue_key: str,
        project: str,
        source: str,
        outcome: TriageRecommendation | TriageFailure,
        run_id: str,
    ) -> Any:
        from triage_service.core.triage_action_applied import TriageActionAppliedFlags

        _ = (issue_key, project, source)
        self.calls.append((issue, outcome, run_id))
        return TriageActionAppliedFlags()


class _RecordingAuditStore:
    def __init__(self) -> None:
        self.events: list[object] = []

    def record(self, event: object) -> None:
        self.events.append(event)


def _app_settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> AppSettings:
    monkeypatch.setenv("JIRA_API_KEY", "jira-api-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-token")
    monkeypatch.setenv("TRIAGE_WEBHOOK_TOKEN", "triage-token")
    monkeypatch.setenv("JIRA_CLOUD_ID", "cloud-id-test")
    monkeypatch.setenv("JIRA_USER_EMAIL", "bot@example.com")
    if "TRIAGE_AUTO_APPLY_DEESCALATION" not in env:
        monkeypatch.setenv("TRIAGE_AUTO_APPLY_DEESCALATION", "false")
    if "TRIAGE_AUTO_APPLY_ESCALATION" not in env:
        monkeypatch.setenv("TRIAGE_AUTO_APPLY_ESCALATION", "false")
    if "TRIAGE_AUTO_APPLY_BUG_TO_STORY" not in env:
        monkeypatch.setenv("TRIAGE_AUTO_APPLY_BUG_TO_STORY", "false")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
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
                settings=settings,
            )
            sync_result = handler.run_sync(
                issue_key="TJC-9",
                project="TJC",
                source="bug_created",
                run_id="run-correlation-1",
            )
            outcome = sync_result.outcome

    assert isinstance(outcome, TriageRecommendation)
    assert outcome.recommended_issue_type == "Story"
    assert outcome.recommended_priority is None
    assert sync_result.classification is not None
    assert sync_result.classification.recommended_issue_type == "Story"
    assert sync_result.priority is None
    assert len(executor.calls) == 1
    applied_issue, applied_outcome, applied_run_id = executor.calls[0]
    assert applied_issue == issue
    assert applied_outcome == outcome
    assert applied_run_id == "run-correlation-1"


@pytest.mark.unit
def test_handler_enriches_issue_with_linked_zendesk_tickets_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _app_settings(monkeypatch)
    issue = FetchedIssue(
        issue_key="TJC-90",
        summary="login issue references ZD-99",
        issue_type="Bug",
        priority="P2",
        reporter="support",
    )

    def jira_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_jira_payload_for(issue))

    story_json = '{"recommended_issue_type":"Story","confidence":0.7,"reason":"Narrative work."}'

    def openrouter_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": story_json}}]},
        )

    class _StubZendeskFetcher(ZendeskTicketFetcher):
        @property
        def enabled(self) -> bool:  # pragma: no cover - trivial override
            return True

        def fetch_tickets_by_ids_with_failures(
            self,
            ticket_ids: list[str],
        ) -> ZendeskTicketsFetchResult:
            _ = ticket_ids
            return ZendeskTicketsFetchResult(
                tickets=[
                    LinkedZendeskTicket(
                        ticket_id="99",
                        subject="Portal sign-in broken",
                        status="open",
                        priority="urgent",
                        description="Customer blocked",
                        url="https://acme.zendesk.com/agent/tickets/99",
                    ),
                ],
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
                zendesk_fetcher=_StubZendeskFetcher(settings),
                settings=settings,
            )
            sync_result = handler.run_sync(
                issue_key="TJC-90",
                project="TJC",
                source="bug_created",
                run_id="run-correlation-zd",
            )
    applied_issue, _, _ = executor.calls[0]
    assert applied_issue is not None
    assert len(applied_issue.zendesk_tickets) == 1
    assert applied_issue.zendesk_tickets[0].ticket_id == "99"
    assert sync_result.zendesk_context is not None
    assert sync_result.zendesk_context.tickets_fetched == 1


@pytest.mark.unit
def test_handler_applies_zendesk_resolution_summary_from_summarizer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRIAGE_ZENDESK_COMMENT_SUMMARY_ENABLED", "true")
    settings = _app_settings(monkeypatch)
    issue = FetchedIssue(
        issue_key="TJC-91",
        summary="linked zendesk ZD-55",
        issue_type="Bug",
        priority="P3",
        reporter="support",
    )
    base_ticket = LinkedZendeskTicket(
        ticket_id="55",
        subject="Outage thread",
        description="Peak severity opening text.",
        status="solved",
        priority="urgent",
    )
    summarized_ticket = base_ticket.model_copy(
        update={
            "resolution_summary": ZendeskResolutionSummary(
                initial_impact="Major outage reported.",
                latest_status="Third-party outage resolved.",
                resolution_hints="Temporary external cause.",
                open_risks="(none)",
            ),
        },
    )

    class _StubZendeskFetcher(ZendeskTicketFetcher):
        @property
        def enabled(self) -> bool:
            return True

        def fetch_tickets_by_ids_with_failures(
            self,
            ticket_ids: list[str],
        ) -> ZendeskTicketsFetchResult:
            _ = ticket_ids
            return ZendeskTicketsFetchResult(tickets=[base_ticket])

    class _StubSummarizer:
        def summarize(
            self,
            issue: FetchedIssue,
            tickets: list[LinkedZendeskTicket],
            *,
            run_id: str,
        ) -> ZendeskSummarizationResult:
            _ = (issue, run_id)
            return ZendeskSummarizationResult(
                tickets=[summarized_ticket],
                tickets_considered=1,
                tickets_summarized=1,
            )

    def jira_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_jira_payload_for(issue))

    priority_json = (
        '{"recommended_priority":"P3","confidence":0.8,'
        '"reason":"Current impact is normal after recovery."}'
    )
    classification_json = (
        '{"recommended_issue_type":"Bug","confidence":0.9,"reason":"Defect."}'
    )
    responses = [classification_json, priority_json]

    def openrouter_handler(request: httpx.Request) -> httpx.Response:
        body = responses.pop(0)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": body}}]},
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
                zendesk_fetcher=_StubZendeskFetcher(settings),
                zendesk_summarizer=_StubSummarizer(),
                settings=settings,
            )
            sync_result = handler.run_sync(
                issue_key="TJC-91",
                project="TJC",
                source="bug_created",
                run_id="run-zd-summary",
            )
    applied_issue, _, _ = executor.calls[0]
    assert applied_issue is not None
    assert applied_issue.zendesk_tickets[0].resolution_summary is not None
    assert "resolved" in applied_issue.zendesk_tickets[0].resolution_summary.latest_status.lower()
    assert sync_result.zendesk_context is not None
    assert sync_result.zendesk_context.tickets_summarized == 1


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
                settings=settings,
            )
            sync_result = handler.run_sync(
                issue_key="TJC-10",
                project="TJC",
                source="bug_created",
                run_id="run-correlation-2",
            )
            outcome = sync_result.outcome

    assert isinstance(outcome, TriageRecommendation)
    assert outcome.recommended_issue_type == "Bug"
    assert outcome.recommended_priority == "P1"
    assert outcome.confidence == 0.88
    assert outcome.reason == "Data loss risk."
    assert sync_result.classification is not None
    assert sync_result.classification.recommended_issue_type == "Bug"
    assert sync_result.classification.confidence == 0.55
    assert sync_result.priority is not None
    assert sync_result.priority.recommended_priority == "P1"
    assert sync_result.priority.confidence == 0.88
    assert idx["i"] == 2


@pytest.mark.unit
def test_handler_priority_only_project_skips_classification_and_runs_priority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _app_settings(monkeypatch, TRIAGE_PRIORITY_ONLY_PROJECTS="CLOSM")
    issue = FetchedIssue(
        issue_key="CLOSM-10",
        summary="crash",
        description="segfault",
        issue_type="Bug",
        priority="P2",
        reporter="bob",
    )

    def jira_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_jira_payload_for(issue))

    pri_json = '{"recommended_priority":"P1","confidence":0.88,"reason":"Data loss risk."}'
    idx = {"i": 0}

    def openrouter_handler(request: httpx.Request) -> httpx.Response:
        idx["i"] += 1
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": pri_json}}]},
        )

    audit = _RecordingAuditStore()
    transport_j = httpx.MockTransport(jira_handler)
    transport_o = httpx.MockTransport(openrouter_handler)
    with httpx.Client(transport=transport_j) as j_client:
        with httpx.Client(transport=transport_o) as o_client:
            fetcher = JiraIssueFetcher(settings, client=j_client)
            inference = OpenRouterInferenceClient(settings, client=o_client)
            executor = _RecordingExecutor()
            handler = TriageHandler(
                allowed_projects=("CLOSM",),
                fetcher=fetcher,
                inference=inference,
                policy=_policy(),
                executor=executor,
                audit_store=audit,
                settings=settings,
            )
            sync_result = handler.run_sync(
                issue_key="CLOSM-10",
                project="CLOSM",
                source="bug_created",
                run_id="run-priority-only",
            )
            outcome = sync_result.outcome

    assert idx["i"] == 1
    assert isinstance(outcome, TriageRecommendation)
    assert outcome.recommended_issue_type == "Bug"
    assert outcome.recommended_priority == "P1"
    assert outcome.confidence == 0.88
    assert outcome.reason == "Data loss risk."
    assert sync_result.classification is None
    assert sync_result.priority is not None
    assert sync_result.priority.recommended_priority == "P1"
    event_types = [type(event) for event in audit.events]
    assert ClassificationCompletedAuditEvent not in event_types
    assert isinstance(audit.events[0], PriorityCompletedAuditEvent)
    assert isinstance(audit.events[1], TriageCompletedAuditEvent)
    completed = audit.events[1]
    assert completed.telemetry is not None
    assert completed.telemetry["triage_pipeline"] == "priority_only"


@pytest.mark.unit
def test_handler_priority_only_project_proceeds_when_jira_type_is_story(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _app_settings(monkeypatch, TRIAGE_PRIORITY_ONLY_PROJECTS="CLOSM")
    issue = FetchedIssue(
        issue_key="CLOSM-11",
        summary="request",
        description="please add filter",
        issue_type="Story",
        priority="P3",
        reporter="alice",
    )

    def jira_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_jira_payload_for(issue))

    pri_json = '{"recommended_priority":"P4","confidence":0.6,"reason":"Nice to have."}'

    def openrouter_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": pri_json}}]},
        )

    transport_j = httpx.MockTransport(jira_handler)
    transport_o = httpx.MockTransport(openrouter_handler)
    with httpx.Client(transport=transport_j) as j_client:
        with httpx.Client(transport=transport_o) as o_client:
            fetcher = JiraIssueFetcher(settings, client=j_client)
            inference = OpenRouterInferenceClient(settings, client=o_client)
            handler = TriageHandler(
                allowed_projects=("CLOSM",),
                fetcher=fetcher,
                inference=inference,
                policy=_policy(),
                executor=_RecordingExecutor(),
                settings=settings,
            )
            sync_result = handler.run_sync(
                issue_key="CLOSM-11",
                project="CLOSM",
                source="bug_created",
                run_id="run-priority-only-story-intake",
            )
            outcome = sync_result.outcome

    assert isinstance(outcome, TriageRecommendation)
    assert outcome.recommended_issue_type == "Bug"
    assert outcome.recommended_priority == "P4"
    assert sync_result.classification is None


@pytest.mark.unit
def test_handler_non_priority_only_project_keeps_full_classification_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _app_settings(monkeypatch, TRIAGE_PRIORITY_ONLY_PROJECTS="CLOSM")
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
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": responses[i]}}]},
        )

    audit = _RecordingAuditStore()
    transport_j = httpx.MockTransport(jira_handler)
    transport_o = httpx.MockTransport(openrouter_handler)
    with httpx.Client(transport=transport_j) as j_client:
        with httpx.Client(transport=transport_o) as o_client:
            fetcher = JiraIssueFetcher(settings, client=j_client)
            inference = OpenRouterInferenceClient(settings, client=o_client)
            handler = TriageHandler(
                allowed_projects=("TJC", "CLOSM"),
                fetcher=fetcher,
                inference=inference,
                policy=_policy(),
                executor=_RecordingExecutor(),
                audit_store=audit,
                settings=settings,
            )
            sync_result = handler.run_sync(
                issue_key="TJC-10",
                project="TJC",
                source="bug_created",
                run_id="run-full-path",
            )
            outcome = sync_result.outcome

    assert idx["i"] == 2
    assert isinstance(outcome, TriageRecommendation)
    assert sync_result.classification is not None
    assert isinstance(audit.events[0], ClassificationCompletedAuditEvent)
    completed = audit.events[-1]
    assert isinstance(completed, TriageCompletedAuditEvent)
    assert completed.telemetry is not None
    assert completed.telemetry["triage_pipeline"] == "full"


@pytest.mark.unit
def test_handler_requests_json_object_response_for_both_inference_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _app_settings(monkeypatch)
    issue = FetchedIssue(
        issue_key="TJC-13",
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
    captured_bodies: list[dict[str, Any]] = []

    def openrouter_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        captured_bodies.append(body)
        content = responses[len(captured_bodies) - 1]
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
                settings=settings,
            )
            handler.run_sync(
                issue_key="TJC-13",
                project="TJC",
                source="bug_created",
                run_id="run-json-mode",
            )

    assert len(captured_bodies) == 2
    for body in captured_bodies:
        assert body["response_format"] == {"type": "json_object"}
        assert body["provider"] == {"require_parameters": True}


@pytest.mark.unit
def test_handler_emits_stage_timing_for_fetch_model_and_executor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _app_settings(monkeypatch)
    issue = FetchedIssue(
        issue_key="TJC-12",
        summary="s",
        description="d",
        issue_type="Bug",
        priority="P3",
        reporter="r",
    )

    def jira_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_jira_payload_for(issue))

    cls_json = '{"recommended_issue_type":"Bug","confidence":0.51,"reason":"Defect."}'
    pri_json = '{"recommended_priority":"P1","confidence":0.8,"reason":"Customer impact."}'
    responses = [cls_json, pri_json]
    idx = {"i": 0}

    def openrouter_handler(request: httpx.Request) -> httpx.Response:
        i = idx["i"]
        idx["i"] = i + 1
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": responses[i]}}]},
        )

    perf_values = iter([1.0, 1.01, 1.015, 1.02, 2.0, 2.02, 3.0, 3.04, 4.0, 4.03])
    monkeypatch.setattr(
        "triage_service.core.triage_handler.perf_counter",
        lambda: next(perf_values),
    )
    logger = MagicMock()
    monkeypatch.setattr("triage_service.core.triage_handler.LOGGER", logger)

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
                settings=settings,
            )
            _ = handler.run_sync(
                issue_key="TJC-12",
                project="TJC",
                source="bug_created",
                run_id="run-correlation-latency",
            )

    stage_names = [
        call.kwargs["extra"]["stage"]
        for call in logger.info.call_args_list
        if call.kwargs.get("extra", {}).get("event_type") == "triage_stage_timing"
    ]
    assert stage_names == [
        "jira_fetch",
        "image_context_extraction",
        "classification_inference",
        "priority_inference",
        "jira_action",
    ]
    assert len(stage_names) == 5


@pytest.mark.unit
def test_run_sync_on_fetched_story_path_skips_jira_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _app_settings(monkeypatch)
    issue = FetchedIssue(
        issue_key="TJC-11",
        summary="s",
        description=None,
        issue_type="Bug",
        priority="Medium",
        reporter="r",
    )

    def jira_handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("fetcher must not be used by run_sync_on_fetched")

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
                settings=settings,
            )
            sync_result = handler.run_sync_on_fetched(
                issue=issue,
                project="TJC",
                source="manual_trigger",
                run_id="run-correlation-3",
            )
            outcome = sync_result.outcome

    assert isinstance(outcome, TriageRecommendation)
    assert outcome.recommended_issue_type == "Story"
    assert len(executor.calls) == 1
    applied_issue, applied_outcome, applied_run_id = executor.calls[0]
    assert applied_issue == issue
    assert applied_outcome == outcome
    assert applied_run_id == "run-correlation-3"


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
                settings=settings,
            )
            sync_result = handler.run_sync(
                issue_key="XX-1",
                project="XX",
                source="bug_created",
                run_id="run-correlation-4",
            )
            outcome = sync_result.outcome

    assert isinstance(outcome, TriageFailure)
    assert outcome.category == "project_not_allowed"
    assert len(executor.calls) == 1
    assert executor.calls[0][0] is None
    assert executor.calls[0][1] == outcome
    assert executor.calls[0][2] == "run-correlation-4"


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
                settings=settings,
            )
            sync_result = handler.run_sync(
                issue_key="TJC-1",
                project="TJC",
                source="bug_created",
                run_id="run-correlation-5",
            )
            outcome = sync_result.outcome

    assert isinstance(outcome, TriageFailure)
    assert outcome.category == "jira_fetch_failed"
    assert executor.calls[0][0] is None
    assert executor.calls[0][1] == outcome
    assert executor.calls[0][2] == "run-correlation-5"


class _RecordingTracer:
    """Minimal duck-typed tracer double that records ``model_generation`` finish calls."""

    def __init__(self) -> None:
        self.generations: list[dict[str, Any]] = []

    def flush(self) -> None:
        return None

    @contextmanager
    def triage_run_session(self, *, run_id: str) -> Any:
        _ = run_id
        yield

    @contextmanager
    def triage_issue_trace(self, *, run_id: str, issue_key: str, project: str) -> Any:
        _ = (run_id, issue_key, project)
        yield

    @contextmanager
    def image_context_extraction(self) -> Any:
        yield lambda **_kwargs: None

    @contextmanager
    def zendesk_context_fetch(self) -> Any:
        yield lambda **_kwargs: None

    @contextmanager
    def zendesk_context_summary(self) -> Any:
        yield lambda **_kwargs: None

    @contextmanager
    def model_generation(
        self,
        *,
        step: str,
        model: str,
        messages: list[dict[str, str]],
        model_parameters: dict[str, Any],
    ) -> Any:
        _ = (model, messages, model_parameters)
        record: dict[str, Any] = {"step": step, "finished": False}
        self.generations.append(record)

        def finish(
            raw: str,
            meta: dict[str, Any],
            *,
            usage_details: dict[str, int] | None = None,
            cost_details: dict[str, float] | None = None,
        ) -> None:
            record["finished"] = True
            record["raw"] = raw
            record["meta"] = meta
            record["usage_details"] = usage_details
            record["cost_details"] = cost_details

        yield finish


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


@pytest.mark.unit
def test_handler_bug_path_emits_classification_priority_and_triage_completed_audit_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _app_settings(monkeypatch)
    issue = FetchedIssue(
        issue_key="TJC-10",
        summary="crash",
        description="segfault",
        issue_type="Bug",
        priority="P2",
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

    audit = _RecordingAuditStore()
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
                audit_store=audit,
                settings=settings,
            )
            _ = handler.run_sync(
                issue_key="TJC-10",
                project="TJC",
                source="bug_created",
                run_id="run-audit-bug",
            )

    assert len(audit.events) == 3
    assert isinstance(audit.events[0], ClassificationCompletedAuditEvent)
    assert isinstance(audit.events[1], PriorityCompletedAuditEvent)
    assert isinstance(audit.events[2], TriageCompletedAuditEvent)
    e0 = audit.events[0]
    assert e0.run_id == "run-audit-bug"
    assert e0.issue_key == "TJC-10"
    assert e0.recommended_issue_type == "Bug"
    e2 = audit.events[2]
    assert e2.telemetry == {
        "intake_issue_type": "Bug",
        "intake_priority": "P2",
        "image_context_attachments_considered": 0,
        "image_context_attachments_extracted": 0,
        "auto_apply_deescalation_enabled": False,
        "auto_apply_escalation_enabled": False,
        "auto_apply_bug_to_story_enabled": False,
        "priority_signal": "prioritize",
        "jira_priority": "P2",
        "would_post_jira_comment": True,
        "would_auto_apply_priority_change": False,
        "triage_pipeline": "full",
    }


@pytest.mark.unit
def test_handler_story_path_emits_classification_and_triage_completed_without_priority_event(
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

    audit = _RecordingAuditStore()
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
                audit_store=audit,
                settings=settings,
            )
            _ = handler.run_sync(
                issue_key="TJC-9",
                project="TJC",
                source="priority_changed",
                run_id="run-audit-story",
            )

    assert len(audit.events) == 2
    assert isinstance(audit.events[0], ClassificationCompletedAuditEvent)
    assert isinstance(audit.events[1], TriageCompletedAuditEvent)
    assert audit.events[1].recommended_issue_type == "Story"
    assert audit.events[1].recommended_priority is None
    assert audit.events[1].telemetry == {
        "intake_issue_type": "Bug",
        "intake_priority": "P3",
        "image_context_attachments_considered": 0,
        "image_context_attachments_extracted": 0,
        "auto_apply_deescalation_enabled": False,
        "auto_apply_escalation_enabled": False,
        "auto_apply_bug_to_story_enabled": False,
        "would_post_jira_comment": True,
        "would_auto_apply_issue_type_change": False,
        "triage_pipeline": "full",
    }


@pytest.mark.unit
def test_handler_bug_deescalation_telemetry_respects_auto_apply_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _app_settings(monkeypatch, TRIAGE_AUTO_APPLY_DEESCALATION="true")
    issue = FetchedIssue(
        issue_key="TJC-20",
        summary="latency",
        description="slow",
        issue_type="Bug",
        priority="P1",
        reporter="bob",
    )

    def jira_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_jira_payload_for(issue))

    cls_json = '{"recommended_issue_type":"Bug","confidence":0.55,"reason":"Defect."}'
    pri_json = '{"recommended_priority":"P3","confidence":0.88,"reason":"Minor impact."}'
    responses = [cls_json, pri_json]
    idx = {"i": 0}

    def openrouter_handler(request: httpx.Request) -> httpx.Response:
        i = idx["i"]
        idx["i"] = i + 1
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": responses[i]}}]},
        )

    audit = _RecordingAuditStore()
    with httpx.Client(transport=httpx.MockTransport(jira_handler)) as j_client:
        with httpx.Client(transport=httpx.MockTransport(openrouter_handler)) as o_client:
            fetcher = JiraIssueFetcher(settings, client=j_client)
            inference = OpenRouterInferenceClient(settings, client=o_client)
            handler = TriageHandler(
                allowed_projects=("TJC",),
                fetcher=fetcher,
                inference=inference,
                policy=_policy(),
                executor=_RecordingExecutor(),
                audit_store=audit,
                settings=settings,
            )
            _ = handler.run_sync(
                issue_key="TJC-20",
                project="TJC",
                source="bug_created",
                run_id="run-audit-bug-deescalate",
            )

    event = audit.events[2]
    assert isinstance(event, TriageCompletedAuditEvent)
    assert event.telemetry is not None
    assert event.telemetry["priority_signal"] == "deescalate"
    assert event.telemetry["would_post_jira_comment"] is True
    assert event.telemetry["would_auto_apply_priority_change"] is True
    assert event.telemetry["auto_apply_deescalation_enabled"] is True


@pytest.mark.unit
def test_handler_bug_escalation_telemetry_respects_auto_apply_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _app_settings(monkeypatch, TRIAGE_AUTO_APPLY_ESCALATION="true")
    issue = FetchedIssue(
        issue_key="TJC-22",
        summary="outage",
        description="down",
        issue_type="Bug",
        priority="P3",
        reporter="bob",
    )

    def jira_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_jira_payload_for(issue))

    cls_json = '{"recommended_issue_type":"Bug","confidence":0.55,"reason":"Defect."}'
    pri_json = '{"recommended_priority":"P1","confidence":0.88,"reason":"Critical impact."}'
    responses = [cls_json, pri_json]
    idx = {"i": 0}

    def openrouter_handler(request: httpx.Request) -> httpx.Response:
        i = idx["i"]
        idx["i"] = i + 1
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": responses[i]}}]},
        )

    audit = _RecordingAuditStore()
    with httpx.Client(transport=httpx.MockTransport(jira_handler)) as j_client:
        with httpx.Client(transport=httpx.MockTransport(openrouter_handler)) as o_client:
            fetcher = JiraIssueFetcher(settings, client=j_client)
            inference = OpenRouterInferenceClient(settings, client=o_client)
            handler = TriageHandler(
                allowed_projects=("TJC",),
                fetcher=fetcher,
                inference=inference,
                policy=_policy(),
                executor=_RecordingExecutor(),
                audit_store=audit,
                settings=settings,
            )
            _ = handler.run_sync(
                issue_key="TJC-22",
                project="TJC",
                source="bug_created",
                run_id="run-audit-bug-escalate",
            )

    event = audit.events[2]
    assert isinstance(event, TriageCompletedAuditEvent)
    assert event.telemetry is not None
    assert event.telemetry["priority_signal"] == "prioritize"
    assert event.telemetry["would_post_jira_comment"] is True
    assert event.telemetry["would_auto_apply_priority_change"] is True
    assert event.telemetry["auto_apply_escalation_enabled"] is True


@pytest.mark.unit
def test_handler_story_telemetry_marks_auto_apply_issue_type_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _app_settings(monkeypatch, TRIAGE_AUTO_APPLY_BUG_TO_STORY="true")
    issue = FetchedIssue(
        issue_key="TJC-21",
        summary="feature ask",
        description="add toggle",
        issue_type="Bug",
        priority="P2",
        reporter="alice",
    )

    def jira_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_jira_payload_for(issue))

    story_json = '{"recommended_issue_type":"Story","confidence":0.7,"reason":"Narrative work."}'

    def openrouter_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": story_json}}]},
        )

    audit = _RecordingAuditStore()
    with httpx.Client(transport=httpx.MockTransport(jira_handler)) as j_client:
        with httpx.Client(transport=httpx.MockTransport(openrouter_handler)) as o_client:
            fetcher = JiraIssueFetcher(settings, client=j_client)
            inference = OpenRouterInferenceClient(settings, client=o_client)
            handler = TriageHandler(
                allowed_projects=("TJC",),
                fetcher=fetcher,
                inference=inference,
                policy=_policy(),
                executor=_RecordingExecutor(),
                audit_store=audit,
                settings=settings,
            )
            _ = handler.run_sync(
                issue_key="TJC-21",
                project="TJC",
                source="bug_created",
                run_id="run-audit-story-auto-apply",
            )

    event = audit.events[1]
    assert isinstance(event, TriageCompletedAuditEvent)
    assert event.telemetry is not None
    assert event.telemetry["would_post_jira_comment"] is True
    assert event.telemetry["would_auto_apply_issue_type_change"] is True
    assert event.telemetry["auto_apply_bug_to_story_enabled"] is True


@pytest.mark.unit
def test_handler_story_telemetry_no_auto_apply_for_non_bug_issue_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _app_settings(monkeypatch, TRIAGE_AUTO_APPLY_BUG_TO_STORY="true")
    issue = FetchedIssue(
        issue_key="TJC-22",
        summary="feature ask",
        description="add toggle",
        issue_type="Task",
        priority="P2",
        reporter="alice",
    )

    def jira_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_jira_payload_for(issue))

    story_json = '{"recommended_issue_type":"Story","confidence":0.7,"reason":"Narrative work."}'

    def openrouter_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": story_json}}]},
        )

    audit = _RecordingAuditStore()
    with httpx.Client(transport=httpx.MockTransport(jira_handler)) as j_client:
        with httpx.Client(transport=httpx.MockTransport(openrouter_handler)) as o_client:
            fetcher = JiraIssueFetcher(settings, client=j_client)
            inference = OpenRouterInferenceClient(settings, client=o_client)
            handler = TriageHandler(
                allowed_projects=("TJC",),
                fetcher=fetcher,
                inference=inference,
                policy=_policy(),
                executor=_RecordingExecutor(),
                audit_store=audit,
                settings=settings,
            )
            _ = handler.run_sync(
                issue_key="TJC-22",
                project="TJC",
                source="bug_created",
                run_id="run-audit-story-task",
            )

    event = audit.events[1]
    assert isinstance(event, TriageCompletedAuditEvent)
    assert event.telemetry is not None
    assert event.telemetry["would_post_jira_comment"] is True
    assert event.telemetry["would_auto_apply_issue_type_change"] is False


@pytest.mark.unit
def test_handler_jira_fetch_failure_emits_triage_failed_audit_with_http_telemetry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _app_settings(monkeypatch)
    monkeypatch.setattr(
        "triage_service.adapters.jira_http_retry.time.sleep",
        lambda _s: None,
    )

    def jira_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="unavailable")

    audit = _RecordingAuditStore()
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
                audit_store=audit,
                settings=settings,
            )
            sync_result = handler.run_sync(
                issue_key="TJC-1",
                project="TJC",
                source="bug_created",
                run_id="run-audit-fail",
            )
            outcome = sync_result.outcome

    assert isinstance(outcome, TriageFailure)
    assert len(audit.events) == 1
    ev = audit.events[0]
    assert isinstance(ev, TriageFailedAuditEvent)
    assert ev.category == "jira_fetch_failed"
    assert ev.telemetry is not None
    assert ev.telemetry.get("http_status") == 503
    assert ev.telemetry.get("http_attempts") == settings.jira_http_max_retries + 1
    assert ev.telemetry.get("failure_category") == "http_transient"


@pytest.mark.unit
def test_handler_openrouter_failure_emits_audit_with_resilience_telemetry_and_log(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="triage_service.core.triage_handler")
    settings = _app_settings(monkeypatch)
    monkeypatch.setattr(
        "triage_service.adapters.jira_http_retry.time.sleep",
        lambda *_a, **_k: None,
    )
    issue = FetchedIssue(
        issue_key="TJC-1",
        summary="s",
        description=None,
        issue_type="Bug",
        priority="P2",
        reporter="r",
    )

    def jira_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_jira_payload_for(issue))

    def openrouter_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="unavailable")

    audit = _RecordingAuditStore()
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
                audit_store=audit,
                settings=settings,
            )
            sync_result = handler.run_sync(
                issue_key="TJC-1",
                project="TJC",
                source="bug_created",
                run_id="run-or-fail",
            )
            outcome = sync_result.outcome

    assert isinstance(outcome, TriageFailure)
    assert outcome.category == "inference_failed"
    assert len(audit.events) == 1
    ev = audit.events[0]
    assert isinstance(ev, TriageFailedAuditEvent)
    assert ev.telemetry is not None
    assert ev.telemetry.get("boundary") == "openrouter"
    assert ev.telemetry.get("http_attempts") == settings.openrouter_http_max_retries + 1
    assert ev.telemetry.get("http_status") == 503
    assert ev.telemetry.get("failure_category") == "http_transient"
    assert any(getattr(r, "event_type", None) == "triage_resilience_notice" for r in caplog.records)


@pytest.mark.unit
def test_handler_classification_parse_failure_records_raw_output_and_audit_snippet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _app_settings(monkeypatch)
    issue = FetchedIssue(
        issue_key="TJC-30",
        summary="s",
        description=None,
        issue_type="Bug",
        priority="P2",
        reporter="r",
    )

    def jira_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_jira_payload_for(issue))

    def openrouter_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": "not json at all"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.001},
            },
        )

    audit = _RecordingAuditStore()
    tracer = _RecordingTracer()
    with httpx.Client(transport=httpx.MockTransport(jira_handler)) as j_client:
        with httpx.Client(transport=httpx.MockTransport(openrouter_handler)) as o_client:
            fetcher = JiraIssueFetcher(settings, client=j_client)
            inference = OpenRouterInferenceClient(settings, client=o_client)
            handler = TriageHandler(
                allowed_projects=("TJC",),
                fetcher=fetcher,
                inference=inference,
                policy=_policy(),
                executor=_RecordingExecutor(),
                audit_store=audit,
                inference_tracer=tracer,  # type: ignore[arg-type]
                settings=settings,
            )
            sync_result = handler.run_sync(
                issue_key="TJC-30",
                project="TJC",
                source="bug_created",
                run_id="run-parse-fail",
            )

    assert isinstance(sync_result.outcome, TriageFailure)
    assert sync_result.outcome.category == "invalid_model_output"

    assert len(tracer.generations) == 1
    generation = tracer.generations[0]
    assert generation["finished"] is True
    assert generation["raw"] == "not json at all"
    assert generation["meta"] == {"parse_failure": True}
    assert generation["usage_details"] == {"prompt_tokens": 10, "completion_tokens": 5}
    assert generation["cost_details"] == {"total": 0.001}

    assert len(audit.events) == 1
    event = audit.events[0]
    assert isinstance(event, TriageFailedAuditEvent)
    assert event.telemetry is not None
    assert event.telemetry.get("boundary") == "model_output_parse"
    assert event.telemetry.get("model_output_snippet") == "not json at all"


@pytest.mark.unit
def test_handler_classification_parse_failure_redacts_snippet_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _app_settings(monkeypatch, TRIAGE_AUDIT_REDACT_MODEL_OUTPUT="true")
    issue = FetchedIssue(
        issue_key="TJC-31",
        summary="s",
        description=None,
        issue_type="Bug",
        priority="P2",
        reporter="r",
    )

    def jira_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_jira_payload_for(issue))

    def openrouter_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "still not json"}}]},
        )

    audit = _RecordingAuditStore()
    with httpx.Client(transport=httpx.MockTransport(jira_handler)) as j_client:
        with httpx.Client(transport=httpx.MockTransport(openrouter_handler)) as o_client:
            fetcher = JiraIssueFetcher(settings, client=j_client)
            inference = OpenRouterInferenceClient(settings, client=o_client)
            handler = TriageHandler(
                allowed_projects=("TJC",),
                fetcher=fetcher,
                inference=inference,
                policy=_policy(),
                executor=_RecordingExecutor(),
                audit_store=audit,
                settings=settings,
            )
            sync_result = handler.run_sync(
                issue_key="TJC-31",
                project="TJC",
                source="bug_created",
                run_id="run-parse-fail-redacted",
            )

    assert isinstance(sync_result.outcome, TriageFailure)
    event = audit.events[0]
    assert isinstance(event, TriageFailedAuditEvent)
    assert event.telemetry is not None
    assert "model_output_snippet" not in event.telemetry


@pytest.mark.unit
def test_handler_priority_parse_failure_retries_once_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _app_settings(monkeypatch)
    issue = FetchedIssue(
        issue_key="TJC-40",
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
    responses = [cls_json, "I cannot decide on a priority.", pri_json]
    idx = {"i": 0}

    def openrouter_handler(request: httpx.Request) -> httpx.Response:
        i = idx["i"]
        idx["i"] = i + 1
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": responses[i]}}],
                "usage": {"total_cost": 0.01},
            },
        )

    audit = _RecordingAuditStore()
    tracer = _RecordingTracer()
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
                audit_store=audit,
                inference_tracer=tracer,  # type: ignore[arg-type]
                settings=settings,
            )
            sync_result = handler.run_sync(
                issue_key="TJC-40",
                project="TJC",
                source="bug_created",
                run_id="run-priority-retry",
            )

    assert idx["i"] == 3
    outcome = sync_result.outcome
    assert isinstance(outcome, TriageRecommendation)
    assert outcome.recommended_issue_type == "Bug"
    assert outcome.recommended_priority == "P1"
    assert sync_result.priority is not None
    assert sync_result.priority.recommended_priority == "P1"

    priority_generations = [g for g in tracer.generations if g["step"] == "priority"]
    assert len(priority_generations) == 2
    assert priority_generations[0]["meta"] == {"parse_failure": True, "attempt": 1}
    assert priority_generations[1]["meta"]["attempt"] == 2
    assert priority_generations[1]["meta"]["parsed"]["recommended_priority"] == "P1"

    priority_completed_events = [
        e for e in audit.events if isinstance(e, PriorityCompletedAuditEvent)
    ]
    assert len(priority_completed_events) == 1
    triage_completed_events = [
        e for e in audit.events if isinstance(e, TriageCompletedAuditEvent)
    ]
    assert len(triage_completed_events) == 1


@pytest.mark.unit
def test_handler_priority_parse_failure_twice_returns_invalid_model_output_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _app_settings(monkeypatch)
    issue = FetchedIssue(
        issue_key="TJC-41",
        summary="crash",
        description="segfault",
        issue_type="Bug",
        priority="Low",
        reporter="bob",
    )

    def jira_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_jira_payload_for(issue))

    cls_json = '{"recommended_issue_type":"Bug","confidence":0.55,"reason":"Defect."}'
    responses = [cls_json, "still no json", "still no json again"]
    idx = {"i": 0}

    def openrouter_handler(request: httpx.Request) -> httpx.Response:
        i = idx["i"]
        idx["i"] = i + 1
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": responses[i]}}]},
        )

    audit = _RecordingAuditStore()
    tracer = _RecordingTracer()
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
                audit_store=audit,
                inference_tracer=tracer,  # type: ignore[arg-type]
                settings=settings,
            )
            sync_result = handler.run_sync(
                issue_key="TJC-41",
                project="TJC",
                source="bug_created",
                run_id="run-priority-retry-fail",
            )

    assert idx["i"] == 3
    outcome = sync_result.outcome
    assert isinstance(outcome, TriageFailure)
    assert outcome.category == "invalid_model_output"
    failed_events = [e for e in audit.events if isinstance(e, TriageFailedAuditEvent)]
    assert len(failed_events) == 1
    priority_completed_events = [
        e for e in audit.events if isinstance(e, PriorityCompletedAuditEvent)
    ]
    assert len(priority_completed_events) == 0
    priority_generations = [g for g in tracer.generations if g["step"] == "priority"]
    assert len(priority_generations) == 2
    assert priority_generations[0]["meta"] == {"parse_failure": True, "attempt": 1}
    assert priority_generations[1]["meta"] == {"parse_failure": True, "attempt": 2}


@pytest.mark.unit
def test_handler_priority_parse_failure_retry_sums_both_attempt_costs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _app_settings(monkeypatch)
    issue = FetchedIssue(
        issue_key="TJC-42",
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
    responses = [cls_json, "not json", pri_json]
    costs = [0.01, 0.02, 0.03]
    idx = {"i": 0}

    def openrouter_handler(request: httpx.Request) -> httpx.Response:
        i = idx["i"]
        idx["i"] = i + 1
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": responses[i]}}],
                "usage": {"total_cost": costs[i]},
            },
        )

    analytics = _RecordingAnalyticsClient()
    transport_j = httpx.MockTransport(jira_handler)
    transport_o = httpx.MockTransport(openrouter_handler)
    with httpx.Client(transport=transport_j) as j_client:
        with httpx.Client(transport=transport_o) as o_client:
            fetcher = JiraIssueFetcher(settings, client=j_client)
            inference = OpenRouterInferenceClient(settings, client=o_client)
            handler = TriageHandler(
                allowed_projects=("TJC",),
                fetcher=fetcher,
                inference=inference,
                policy=_policy(),
                executor=_RecordingExecutor(),
                settings=settings,
                analytics_client=analytics,
            )
            handler.run_sync(
                issue_key="TJC-42",
                project="TJC",
                source="bug_created",
                run_id="run-priority-retry-cost",
            )

    assert len(analytics.calls) == 1
    assert analytics.calls[0]["inference_cost_usd"] == pytest.approx(0.06)


@pytest.mark.unit
def test_build_default_triage_handler_local_mock_mode_skips_external_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JIRA_API_KEY", "jira-api-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-token")
    monkeypatch.setenv("TRIAGE_WEBHOOK_TOKEN", "triage-token")
    monkeypatch.setenv("TRIAGE_ALLOWED_PROJECTS", "TJC")
    monkeypatch.setenv("TRIAGE_LOCAL_MOCK_MODE", "1")
    monkeypatch.delenv("JIRA_CLOUD_ID", raising=False)
    monkeypatch.delenv("JIRA_USER_EMAIL", raising=False)

    runner = build_default_triage_handler()
    sync_result = runner.run_sync(
        issue_key="TJC-123",
        project="TJC",
        source="manual_trigger",
        run_id="local-mock-run",
    )
    outcome = sync_result.outcome

    assert isinstance(outcome, TriageRecommendation)
    assert outcome.recommended_issue_type == "Story"
    assert outcome.recommended_priority is None
    assert "local mock mode" in outcome.reason.lower()


@pytest.mark.unit
def test_build_default_triage_handler_builds_dedicated_zendesk_summarizer_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JIRA_API_KEY", "jira-api-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-token")
    monkeypatch.setenv("TRIAGE_WEBHOOK_TOKEN", "triage-token")
    monkeypatch.setenv("TRIAGE_ALLOWED_PROJECTS", "TJC")
    monkeypatch.delenv("JIRA_CLOUD_ID", raising=False)
    monkeypatch.delenv("JIRA_USER_EMAIL", raising=False)
    captured_kwargs: dict[str, object] = {}

    def _capture_summarizer(
        _settings: AppSettings,
        **kwargs: object,
    ) -> NoOpZendeskCommentSummarizer:
        captured_kwargs.update(kwargs)
        return NoOpZendeskCommentSummarizer()

    with patch(
        "triage_service.core.triage_handler.build_zendesk_comment_summarizer",
        side_effect=_capture_summarizer,
    ):
        build_default_triage_handler()

    assert "inference_client" not in captured_kwargs
    assert "inference_tracer" in captured_kwargs


@pytest.mark.unit
def test_triage_completed_telemetry_includes_intake_issue_type_and_priority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from triage_service.core.triage_handler import _triage_completed_telemetry

    settings = _app_settings(monkeypatch)
    issue = FetchedIssue(
        issue_key="TJC-55",
        summary="s",
        description=None,
        issue_type="Bug",
        priority="P3",
        reporter="r",
    )
    recommendation = TriageRecommendation(
        recommended_issue_type="Bug",
        recommended_priority="P2",
        confidence=0.9,
        reason="Escalate.",
    )
    telemetry = _triage_completed_telemetry(
        issue=issue,
        recommendation=recommendation,
        settings=settings,
    )
    assert telemetry is not None
    assert telemetry["intake_issue_type"] == "Bug"
    assert telemetry["intake_priority"] == "P3"
    assert telemetry["triage_pipeline"] == "full"


@pytest.mark.unit
def test_triage_completed_telemetry_nulls_intake_priority_for_story_intake(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from triage_service.core.triage_handler import _triage_completed_telemetry

    settings = _app_settings(monkeypatch)
    issue = FetchedIssue(
        issue_key="TJC-56",
        summary="s",
        description=None,
        issue_type="Story",
        priority="Medium",
        reporter="r",
    )
    recommendation = TriageRecommendation(
        recommended_issue_type="Story",
        recommended_priority=None,
        confidence=0.7,
        reason="Still a story.",
    )
    telemetry = _triage_completed_telemetry(
        issue=issue,
        recommendation=recommendation,
        settings=settings,
    )
    assert telemetry is not None
    assert telemetry["intake_issue_type"] == "Story"
    assert telemetry["intake_priority"] is None


@pytest.mark.unit
def test_triage_completed_telemetry_defaults_intake_priority_to_p3_when_bug_priority_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from triage_service.core.triage_handler import _triage_completed_telemetry

    settings = _app_settings(monkeypatch)
    issue = FetchedIssue(
        issue_key="TJC-57",
        summary="s",
        description=None,
        issue_type="Bug",
        priority=None,
        reporter="r",
    )
    recommendation = TriageRecommendation(
        recommended_issue_type="Bug",
        recommended_priority="P2",
        confidence=0.9,
        reason="Escalate.",
    )
    telemetry = _triage_completed_telemetry(
        issue=issue,
        recommendation=recommendation,
        settings=settings,
    )
    assert telemetry is not None
    assert telemetry["intake_issue_type"] == "Bug"
    assert telemetry["intake_priority"] == "P3"


@pytest.mark.unit
def test_triage_completed_telemetry_defaults_intake_priority_to_p3_when_bug_priority_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from triage_service.core.triage_handler import _triage_completed_telemetry

    settings = _app_settings(monkeypatch)
    issue = FetchedIssue(
        issue_key="TJC-58",
        summary="s",
        description=None,
        issue_type="Bug",
        priority="Medium",
        reporter="r",
    )
    recommendation = TriageRecommendation(
        recommended_issue_type="Bug",
        recommended_priority="P2",
        confidence=0.9,
        reason="Escalate.",
    )
    telemetry = _triage_completed_telemetry(
        issue=issue,
        recommendation=recommendation,
        settings=settings,
    )
    assert telemetry is not None
    assert telemetry["intake_issue_type"] == "Bug"
    assert telemetry["intake_priority"] == "P3"


class _RecordingAnalyticsClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def emit_completed_decision(
        self,
        event: TriageCompletedAuditEvent,
        *,
        applied_type_change: bool,
        applied_priority_change: bool,
        inference_cost_usd: float | None,
        triaged_at: object = None,
        issue_created_at: object = None,
        issue_name: object = None,
    ) -> None:
        _ = (triaged_at, issue_created_at, issue_name)
        self.calls.append(
            {
                "event": event,
                "applied_type_change": applied_type_change,
                "applied_priority_change": applied_priority_change,
                "inference_cost_usd": inference_cost_usd,
            },
        )


@pytest.mark.unit
def test_handler_emits_analytics_decision_after_successful_triage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _app_settings(monkeypatch)
    issue = FetchedIssue(
        issue_key="TJC-57",
        summary="crash",
        description="segfault",
        issue_type="Bug",
        priority="P2",
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
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": responses[i]}}],
                "usage": {"total_cost": 0.012},
            },
        )

    analytics = _RecordingAnalyticsClient()
    with httpx.Client(transport=httpx.MockTransport(jira_handler)) as j_client:
        with httpx.Client(transport=httpx.MockTransport(openrouter_handler)) as o_client:
            fetcher = JiraIssueFetcher(settings, client=j_client)
            inference = OpenRouterInferenceClient(settings, client=o_client)
            handler = TriageHandler(
                allowed_projects=("TJC",),
                fetcher=fetcher,
                inference=inference,
                policy=_policy(),
                executor=_RecordingExecutor(),
                settings=settings,
                analytics_client=analytics,
            )
            handler.run_sync(
                issue_key="TJC-57",
                project="TJC",
                source="manual_trigger",
                run_id="run-analytics",
            )

    assert len(analytics.calls) == 1
    call = analytics.calls[0]
    event = call["event"]
    assert isinstance(event, TriageCompletedAuditEvent)
    assert event.run_id == "run-analytics"
    assert call["applied_type_change"] is False
    assert call["applied_priority_change"] is False
    assert call["inference_cost_usd"] == pytest.approx(0.024)
