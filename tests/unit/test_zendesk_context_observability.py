"""Observability for Zendesk context enrichment (audit, Langfuse, triage telemetry)."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from triage_service.adapters.jira_issue_fetcher import (
    FetchedIssue,
    LinkedZendeskTicket,
    ZendeskResolutionSummary,
)
from triage_service.adapters.zendesk_comment_summarizer import ZendeskSummarizationResult
from triage_service.core.policy_context import PolicyContext
from triage_service.observability.audit_events import (
    TriageCompletedAuditEvent,
    ZendeskContextFetchedAuditEvent,
    ZendeskContextSummarizedAuditEvent,
    parse_triage_audit_event,
)
from triage_service.observability.langfuse_inference_tracing import LangfuseInferenceTracer


@pytest.mark.unit
def test_parse_zendesk_context_fetched_round_trip() -> None:
    payload: dict[str, Any] = {
        "event_type": "zendesk_context_fetched",
        "run_id": "run-zd-fetch",
        "issue_key": "TJC-1",
        "project": "TJC",
        "source": "bug_created",
        "ticket_ids_requested": ["47322", "48661"],
        "tickets_fetched": 1,
        "ticket_ids_deduped": 1,
        "fetch_failed": False,
        "per_ticket_failures": [
            {"ticket_id": "48661", "failure": "http_404"},
        ],
    }
    event = parse_triage_audit_event(payload)
    assert isinstance(event, ZendeskContextFetchedAuditEvent)
    assert event.tickets_fetched == 1
    assert event.ticket_ids_deduped == 1
    assert len(event.per_ticket_failures) == 1


@pytest.mark.unit
def test_parse_zendesk_context_summarized_round_trip() -> None:
    payload: dict[str, Any] = {
        "event_type": "zendesk_context_summarized",
        "run_id": "run-zd-sum",
        "issue_key": "TJC-2",
        "project": "TJC",
        "source": "manual_trigger",
        "tickets_considered": 2,
        "tickets_summarized": 1,
        "total_summary_cost": 0.003,
        "per_ticket": [
            {
                "ticket_id": "47322",
                "summarized": True,
                "inference_cost": 0.003,
                "failure": None,
            },
            {
                "ticket_id": "48661",
                "summarized": False,
                "inference_cost": None,
                "failure": "parse_failed",
            },
        ],
    }
    event = parse_triage_audit_event(payload)
    assert isinstance(event, ZendeskContextSummarizedAuditEvent)
    assert event.tickets_summarized == 1
    assert len(event.per_ticket) == 2


@pytest.mark.unit
def test_tracer_records_zendesk_context_fetch_span_nested_under_pipeline() -> None:
    root_cm = MagicMock()
    fetch_cm = MagicMock()
    root_obs = MagicMock()
    fetch_obs = MagicMock()
    root_cm.__enter__.return_value = root_obs
    fetch_cm.__enter__.return_value = fetch_obs

    @contextmanager
    def root_ctx(**kwargs: Any) -> Any:
        _ = kwargs
        yield root_obs

    @contextmanager
    def fetch_ctx(**kwargs: Any) -> Any:
        _ = kwargs
        yield fetch_obs

    client = MagicMock()
    client.start_as_current_observation.side_effect = [root_ctx(), fetch_ctx()]
    client.get_current_trace_id.return_value = "trace-1"
    client.get_current_observation_id.return_value = "span-1"

    tracer = LangfuseInferenceTracer(client)

    with tracer.triage_issue_trace(run_id="r1", issue_key="TJC-9", project="TJC"):
        with tracer.zendesk_context_fetch() as finish_fetch:
            finish_fetch(
                ticket_ids_requested=2,
                tickets_fetched=1,
                ticket_ids_deduped=1,
                fetch_failed=False,
                per_ticket_failures=1,
            )

    assert client.start_as_current_observation.call_count == 2
    second = client.start_as_current_observation.call_args_list[1]
    assert second.kwargs["name"] == "zendesk_context_fetch"
    assert second.kwargs["as_type"] == "span"
    fetch_obs.update.assert_called_once_with(
        metadata={
            "ticket_ids_requested": 2,
            "tickets_fetched": 1,
            "ticket_ids_deduped": 1,
            "fetch_failed": False,
            "per_ticket_failures": 1,
        },
    )


@pytest.mark.unit
def test_tracer_records_zendesk_context_summary_span_nested_under_pipeline() -> None:
    root_cm = MagicMock()
    summary_cm = MagicMock()
    root_obs = MagicMock()
    summary_obs = MagicMock()
    root_cm.__enter__.return_value = root_obs
    summary_cm.__enter__.return_value = summary_obs

    @contextmanager
    def root_ctx(**kwargs: Any) -> Any:
        _ = kwargs
        yield root_obs

    @contextmanager
    def summary_ctx(**kwargs: Any) -> Any:
        _ = kwargs
        yield summary_obs

    client = MagicMock()
    client.start_as_current_observation.side_effect = [root_ctx(), summary_ctx()]
    client.get_current_trace_id.return_value = "trace-1"
    client.get_current_observation_id.return_value = "span-1"

    tracer = LangfuseInferenceTracer(client)

    with tracer.triage_issue_trace(run_id="r1", issue_key="TJC-9", project="TJC"):
        with tracer.zendesk_context_summary() as finish_summary:
            finish_summary(
                tickets_considered=2,
                tickets_summarized=1,
                total_summary_cost=0.004,
            )

    second = client.start_as_current_observation.call_args_list[1]
    assert second.kwargs["name"] == "zendesk_context_summary"
    summary_obs.update.assert_called_once_with(
        metadata={
            "tickets_considered": 2,
            "tickets_summarized": 1,
            "total_summary_cost": 0.004,
        },
    )


class _RecordingAuditStore:
    def __init__(self) -> None:
        self.events: list[Any] = []

    def record(self, event: Any) -> None:
        self.events.append(event)


class _NoOpExecutor:
    def apply_triage_outcome(self, **kwargs: Any) -> Any:
        from triage_service.core.triage_action_applied import TriageActionAppliedFlags

        _ = kwargs
        return TriageActionAppliedFlags()


def _app_settings(monkeypatch: pytest.MonkeyPatch) -> Any:
    from triage_service.core.settings import load_settings

    monkeypatch.setenv("JIRA_API_KEY", "jira-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    monkeypatch.setenv("JIRA_BASE_URL", "https://example.atlassian.net")
    monkeypatch.setenv("JIRA_USER_EMAIL", "bot@example.com")
    return load_settings()


def _jira_payload_for(issue: FetchedIssue) -> dict[str, Any]:
    return {
        "key": issue.issue_key,
        "fields": {
            "summary": issue.summary,
            "description": issue.description,
            "issuetype": {"name": issue.issue_type},
            "priority": ({"name": issue.priority} if issue.priority else None),
            "reporter": {"displayName": issue.reporter},
            "attachment": [],
        },
    }


@pytest.mark.unit
def test_handler_emits_zendesk_audit_events_and_telemetry_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from triage_service.adapters.jira_issue_fetcher import JiraIssueFetcher
    from triage_service.adapters.openrouter_inference_client import OpenRouterInferenceClient
    from triage_service.adapters.zendesk_ticket_fetcher import (
        ZendeskTicketFetcher,
        ZendeskTicketsFetchResult,
    )
    from triage_service.core.triage_handler import TriageHandler

    monkeypatch.setenv("TRIAGE_ZENDESK_COMMENT_SUMMARY_ENABLED", "true")
    settings = _app_settings(monkeypatch)
    issue = FetchedIssue(
        issue_key="TJC-60",
        summary="linked ZD-47322",
        issue_type="Bug",
        priority="P2",
        reporter="support",
        zendesk_ticket_ids=["47322"],
    )
    linked_ticket = LinkedZendeskTicket(
        ticket_id="47322",
        subject="Outage",
        description="Peak severity",
        status="solved",
        priority="urgent",
    )
    summarized_ticket = linked_ticket.model_copy(
        update={
            "resolution_summary": ZendeskResolutionSummary(
                initial_impact="Major outage.",
                latest_status="Recovered.",
                resolution_hints="Third-party resolved.",
                open_risks="(none)",
            ),
        },
    )

    class _StubZendeskFetcher(ZendeskTicketFetcher):
        @property
        def enabled(self) -> bool:
            return True

        def collect_linked_ticket_ids_with_stats(
            self,
            issue: FetchedIssue,
        ) -> tuple[list[str], int]:
            _ = issue
            return ["47322"], 0

        def fetch_tickets_by_ids_with_failures(
            self,
            ticket_ids: list[str],
        ) -> ZendeskTicketsFetchResult:
            _ = ticket_ids
            return ZendeskTicketsFetchResult(tickets=[linked_ticket])

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
                total_inference_cost=0.002,
                per_ticket=[
                    {
                        "ticket_id": "47322",
                        "summarized": True,
                        "inference_cost": 0.002,
                        "failure": None,
                    },
                ],
            )

    story_json = '{"recommended_issue_type":"Story","confidence":0.8,"reason":"Docs."}'

    def jira_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_jira_payload_for(issue))

    def openrouter_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": story_json}}]},
        )

    audit = _RecordingAuditStore()
    mock_tracer = MagicMock()
    root_cm = MagicMock()
    fetch_cm = MagicMock()
    summary_cm = MagicMock()
    finish_fetch = MagicMock()
    finish_summary = MagicMock()
    root_cm.__enter__ = MagicMock(return_value=None)
    root_cm.__exit__ = MagicMock(return_value=False)
    fetch_cm.__enter__ = MagicMock(return_value=finish_fetch)
    fetch_cm.__exit__ = MagicMock(return_value=False)
    summary_cm.__enter__ = MagicMock(return_value=finish_summary)
    summary_cm.__exit__ = MagicMock(return_value=False)
    mock_tracer.triage_issue_trace.return_value = root_cm
    mock_tracer.zendesk_context_fetch.return_value = fetch_cm
    mock_tracer.zendesk_context_summary.return_value = summary_cm

    with httpx.Client(transport=httpx.MockTransport(jira_handler)) as j_client:
        with httpx.Client(transport=httpx.MockTransport(openrouter_handler)) as o_client:
            handler = TriageHandler(
                allowed_projects=("TJC",),
                fetcher=JiraIssueFetcher(settings, client=j_client),
                inference=OpenRouterInferenceClient(settings, client=o_client),
                policy=PolicyContext(
                    bug_definition="bug",
                    priority_definition="pri",
                ),
                executor=_NoOpExecutor(),
                audit_store=audit,
                zendesk_fetcher=_StubZendeskFetcher(settings),
                zendesk_summarizer=_StubSummarizer(),
                inference_tracer=mock_tracer,
                settings=settings,
            )
            _ = handler.run_sync(
                issue_key="TJC-60",
                project="TJC",
                source="bug_created",
                run_id="run-zd-obs",
            )

    fetched = [e for e in audit.events if isinstance(e, ZendeskContextFetchedAuditEvent)]
    assert len(fetched) == 1
    assert fetched[0].ticket_ids_requested == ["47322"]
    assert fetched[0].tickets_fetched == 1

    summarized = [e for e in audit.events if isinstance(e, ZendeskContextSummarizedAuditEvent)]
    assert len(summarized) == 1
    assert summarized[0].tickets_summarized == 1
    assert summarized[0].total_summary_cost == 0.002

    completed = [e for e in audit.events if isinstance(e, TriageCompletedAuditEvent)]
    assert len(completed) == 1
    assert completed[0].telemetry is not None
    assert completed[0].telemetry["zendesk_tickets_considered"] == 1
    assert completed[0].telemetry["zendesk_tickets_fetched"] == 1
    assert completed[0].telemetry["zendesk_tickets_summarized"] == 1

    mock_tracer.zendesk_context_fetch.assert_called_once_with()
    finish_fetch.assert_called_once()
    mock_tracer.zendesk_context_summary.assert_called_once_with()
    finish_summary.assert_called_once()


@pytest.mark.unit
def test_handler_emits_per_ticket_fetch_failures_when_one_ticket_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from triage_service.adapters.jira_issue_fetcher import JiraIssueFetcher
    from triage_service.adapters.openrouter_inference_client import OpenRouterInferenceClient
    from triage_service.adapters.zendesk_ticket_fetcher import (
        ZendeskTicketFetchFailureRecord,
        ZendeskTicketFetcher,
        ZendeskTicketsFetchResult,
    )
    from triage_service.core.triage_handler import TriageHandler

    settings = _app_settings(monkeypatch)
    issue = FetchedIssue(
        issue_key="TJC-61",
        summary="linked ZD-47322 and ZD-48661",
        issue_type="Bug",
        priority="P2",
        reporter="support",
        zendesk_ticket_ids=["47322", "48661"],
    )
    linked_ticket = LinkedZendeskTicket(
        ticket_id="47322",
        subject="Outage",
        description="Peak severity",
        status="solved",
        priority="urgent",
    )

    class _StubZendeskFetcher(ZendeskTicketFetcher):
        @property
        def enabled(self) -> bool:
            return True

        def collect_linked_ticket_ids_with_stats(
            self,
            issue: FetchedIssue,
        ) -> tuple[list[str], int]:
            _ = issue
            return ["47322", "48661"], 0

        def fetch_tickets_by_ids_with_failures(
            self,
            ticket_ids: list[str],
        ) -> ZendeskTicketsFetchResult:
            assert ticket_ids == ["47322", "48661"]
            return ZendeskTicketsFetchResult(
                tickets=[linked_ticket],
                failures=[
                    ZendeskTicketFetchFailureRecord(
                        ticket_id="48661",
                        failure="http_404",
                    ),
                ],
            )

    story_json = '{"recommended_issue_type":"Story","confidence":0.8,"reason":"Docs."}'

    def jira_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_jira_payload_for(issue))

    def openrouter_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": story_json}}]},
        )

    audit = _RecordingAuditStore()
    mock_tracer = MagicMock()
    root_cm = MagicMock()
    fetch_cm = MagicMock()
    finish_fetch = MagicMock()
    root_cm.__enter__ = MagicMock(return_value=None)
    root_cm.__exit__ = MagicMock(return_value=False)
    fetch_cm.__enter__ = MagicMock(return_value=finish_fetch)
    fetch_cm.__exit__ = MagicMock(return_value=False)
    mock_tracer.triage_issue_trace.return_value = root_cm
    mock_tracer.zendesk_context_fetch.return_value = fetch_cm

    with httpx.Client(transport=httpx.MockTransport(jira_handler)) as j_client:
        with httpx.Client(transport=httpx.MockTransport(openrouter_handler)) as o_client:
            handler = TriageHandler(
                allowed_projects=("TJC",),
                fetcher=JiraIssueFetcher(settings, client=j_client),
                inference=OpenRouterInferenceClient(settings, client=o_client),
                policy=PolicyContext(
                    bug_definition="bug",
                    priority_definition="pri",
                ),
                executor=_NoOpExecutor(),
                audit_store=audit,
                zendesk_fetcher=_StubZendeskFetcher(settings),
                inference_tracer=mock_tracer,
                settings=settings,
            )
            sync_result = handler.run_sync(
                issue_key="TJC-61",
                project="TJC",
                source="bug_created",
                run_id="run-zd-partial",
            )

    assert sync_result.zendesk_context is not None
    assert sync_result.zendesk_context.tickets_fetched == 1
    assert sync_result.zendesk_context.fetch_failed is False

    fetched = [e for e in audit.events if isinstance(e, ZendeskContextFetchedAuditEvent)]
    assert len(fetched) == 1
    assert fetched[0].tickets_fetched == 1
    assert len(fetched[0].per_ticket_failures) == 1
    assert fetched[0].per_ticket_failures[0].ticket_id == "48661"
    assert fetched[0].per_ticket_failures[0].failure == "http_404"

    finish_fetch.assert_called_once_with(
        ticket_ids_requested=2,
        tickets_fetched=1,
        ticket_ids_deduped=0,
        fetch_failed=False,
        per_ticket_failures=1,
    )
