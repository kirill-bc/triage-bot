"""Observability for image context extraction (audit, Langfuse, triage telemetry)."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from triage_service.adapters.image_context_extractor import (
    ImageAttachmentMetric,
    ImageContext,
    ImageContextExtractionResult,
    NoOpImageContextExtractor,
)
from triage_service.adapters.jira_issue_fetcher import AttachmentRef, FetchedIssue
from triage_service.core.policy_context import PolicyContext
from triage_service.observability.audit_events import (
    ImageContextExtractedAuditEvent,
    TriageCompletedAuditEvent,
    TriageFailedAuditEvent,
    parse_triage_audit_event,
)
from triage_service.observability.langfuse_inference_tracing import LangfuseInferenceTracer


@pytest.mark.unit
def test_parse_image_context_extracted_round_trip() -> None:
    payload: dict[str, Any] = {
        "event_type": "image_context_extracted",
        "run_id": "run-img-audit",
        "issue_key": "TJC-1",
        "project": "TJC",
        "source": "bug_created",
        "attachments_considered": 2,
        "attachments_extracted": 1,
        "total_bytes": 4096,
        "total_vision_cost": 0.0025,
        "per_attachment": [
            {
                "attachment_id": "a1",
                "filename": "one.png",
                "latency_ms": 120.5,
                "bytes_fetched": 2048,
                "extraction_failure": None,
            },
            {
                "attachment_id": "a2",
                "filename": "two.png",
                "latency_ms": 80.0,
                "bytes_fetched": 2048,
                "extraction_failure": "vision extraction failed: timeout",
            },
        ],
    }
    event = parse_triage_audit_event(payload)
    assert isinstance(event, ImageContextExtractedAuditEvent)
    assert event.attachments_extracted == 1
    assert len(event.per_attachment) == 2


@pytest.mark.unit
def test_tracer_records_image_context_extraction_span_nested_under_pipeline() -> None:
    root_cm = MagicMock()
    img_cm = MagicMock()
    root_obs = MagicMock()
    img_obs = MagicMock()
    root_cm.__enter__.return_value = root_obs
    img_cm.__enter__.return_value = img_obs

    @contextmanager
    def root_ctx(**kwargs: Any) -> Any:
        _ = kwargs
        yield root_obs

    @contextmanager
    def img_ctx(**kwargs: Any) -> Any:
        _ = kwargs
        yield img_obs

    client = MagicMock()
    client.start_as_current_observation.side_effect = [root_ctx(), img_ctx()]

    tracer = LangfuseInferenceTracer(client)

    with tracer.triage_issue_trace(run_id="r1", issue_key="TJC-9", project="TJC"):
        with tracer.image_context_extraction() as finish_img:
            finish_img(
                attachments_considered=2,
                attachments_extracted=1,
                total_bytes=5000,
                total_vision_cost=0.01,
            )

    assert client.start_as_current_observation.call_count == 2
    second = client.start_as_current_observation.call_args_list[1]
    assert second.kwargs["name"] == "image_context_extraction"
    assert second.kwargs["as_type"] == "span"
    img_obs.update.assert_called_once_with(
        metadata={
            "attachments_considered": 2,
            "attachments_extracted": 1,
            "total_bytes": 5000,
            "total_vision_cost": 0.01,
        },
    )


@pytest.mark.unit
def test_tracer_image_context_span_noop_without_client() -> None:
    tracer = LangfuseInferenceTracer(None)
    with tracer.triage_issue_trace(run_id="r1", issue_key="TJC-1", project="TJC"):
        with tracer.image_context_extraction() as finish_img:
            finish_img(
                attachments_considered=0,
                attachments_extracted=0,
                total_bytes=0,
                total_vision_cost=None,
            )


class _RecordingAuditStore:
    def __init__(self) -> None:
        self.events: list[Any] = []

    def record(self, event: Any) -> None:
        self.events.append(event)


class _NoOpExecutor:
    def apply_triage_outcome(self, **kwargs: Any) -> None:
        _ = kwargs


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
            "attachment": [
                {
                    "id": ref.id,
                    "filename": ref.filename,
                    "mimeType": ref.mime_type,
                    "size": ref.size_bytes,
                }
                for ref in issue.attachments
            ],
        },
    }


@pytest.mark.unit
def test_handler_emits_image_context_extracted_and_telemetry_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from triage_service.adapters.jira_issue_fetcher import JiraIssueFetcher
    from triage_service.adapters.openrouter_inference_client import OpenRouterInferenceClient
    from triage_service.core.triage_handler import TriageHandler

    settings = _app_settings(monkeypatch)
    issue = FetchedIssue(
        issue_key="TJC-50",
        summary="crash",
        description="see image",
        issue_type="Bug",
        priority="P2",
        reporter="bob",
        attachments=[
            AttachmentRef(
                id="att-1",
                filename="err.png",
                mime_type="image/png",
                size_bytes=100,
                inline=True,
            ),
        ],
    )
    extraction = ImageContextExtractionResult(
        contexts=[
            ImageContext(
                attachment_id="att-1",
                filename="err.png",
                transcript="Error text",
                summary="Red toast.",
            ),
        ],
        attachments_considered=1,
        attachments_extracted=1,
        total_bytes=2048,
        total_vision_cost=0.001,
        per_attachment=[
            ImageAttachmentMetric(
                attachment_id="att-1",
                filename="err.png",
                latency_ms=50.0,
                bytes_fetched=2048,
            ),
        ],
    )
    mock_extractor = MagicMock()
    mock_extractor.extract.return_value = extraction

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
    img_cm = MagicMock()
    finish_img = MagicMock()
    root_cm.__enter__ = MagicMock(return_value=None)
    root_cm.__exit__ = MagicMock(return_value=False)
    img_cm.__enter__ = MagicMock(return_value=finish_img)
    img_cm.__exit__ = MagicMock(return_value=False)
    mock_tracer.triage_issue_trace.return_value = root_cm
    mock_tracer.image_context_extraction.return_value = img_cm

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
                image_context_extractor=mock_extractor,
                inference_tracer=mock_tracer,
                settings=settings,
            )
            _ = handler.run_sync(
                issue_key="TJC-50",
                project="TJC",
                source="bug_created",
                run_id="run-img-obs",
            )

    img_events = [e for e in audit.events if isinstance(e, ImageContextExtractedAuditEvent)]
    assert len(img_events) == 1
    img_ev = img_events[0]
    assert img_ev.attachments_considered == 1
    assert img_ev.attachments_extracted == 1
    assert img_ev.total_bytes == 2048

    completed = [e for e in audit.events if isinstance(e, TriageCompletedAuditEvent)]
    assert len(completed) == 1
    assert completed[0].telemetry is not None
    assert completed[0].telemetry["image_context_attachments_considered"] == 1
    assert completed[0].telemetry["image_context_attachments_extracted"] == 1

    mock_tracer.image_context_extraction.assert_called_once_with()
    finish_img.assert_called_once_with(
        attachments_considered=1,
        attachments_extracted=1,
        total_bytes=2048,
        total_vision_cost=0.001,
    )


@pytest.mark.unit
def test_handler_triage_failed_includes_image_context_telemetry_when_extraction_ran(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from triage_service.adapters.jira_issue_fetcher import JiraIssueFetcher
    from triage_service.adapters.openrouter_inference_client import OpenRouterInferenceClient
    from triage_service.core.triage_handler import TriageHandler

    settings = _app_settings(monkeypatch)
    issue = FetchedIssue(
        issue_key="TJC-51",
        summary="s",
        description=None,
        issue_type="Bug",
        priority="P2",
        reporter="r",
    )
    extraction = ImageContextExtractionResult(
        contexts=[],
        attachments_considered=0,
        attachments_extracted=0,
        total_bytes=0,
    )
    mock_extractor = MagicMock()
    mock_extractor.extract.return_value = extraction

    def jira_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_jira_payload_for(issue))

    def openrouter_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="unavailable")

    audit = _RecordingAuditStore()
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
                image_context_extractor=mock_extractor,
                settings=settings,
            )
            sync_result = handler.run_sync(
                issue_key="TJC-51",
                project="TJC",
                source="bug_created",
                run_id="run-img-fail",
            )

    from triage_service.core.triage_fallback import TriageFailure

    outcome = sync_result.outcome
    assert isinstance(outcome, TriageFailure)
    failed = [e for e in audit.events if isinstance(e, TriageFailedAuditEvent)]
    assert len(failed) == 1
    assert failed[0].telemetry is not None
    assert failed[0].telemetry["image_context_attachments_considered"] == 0
    assert failed[0].telemetry["image_context_attachments_extracted"] == 0


@pytest.mark.unit
def test_handler_image_context_extract_unexpected_error_soft_fails_and_continues_triage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from triage_service.adapters.jira_issue_fetcher import JiraIssueFetcher
    from triage_service.adapters.openrouter_inference_client import OpenRouterInferenceClient
    from triage_service.core.triage_handler import TriageHandler
    from triage_service.core.triage_recommendation_parser import TriageRecommendation

    settings = _app_settings(monkeypatch)
    issue = FetchedIssue(
        issue_key="TJC-52",
        summary="docs",
        description=None,
        issue_type="Bug",
        priority="P2",
        reporter="r",
    )
    mock_extractor = MagicMock()
    mock_extractor.extract.side_effect = RuntimeError("vision pipeline exploded")

    story_json = '{"recommended_issue_type":"Story","confidence":0.75,"reason":"Docs."}'

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
    img_cm = MagicMock()
    finish_img = MagicMock()
    root_cm.__enter__ = MagicMock(return_value=None)
    root_cm.__exit__ = MagicMock(return_value=False)
    img_cm.__enter__ = MagicMock(return_value=finish_img)
    img_cm.__exit__ = MagicMock(return_value=False)
    mock_tracer.triage_issue_trace.return_value = root_cm
    mock_tracer.image_context_extraction.return_value = img_cm

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
                image_context_extractor=mock_extractor,
                inference_tracer=mock_tracer,
                settings=settings,
            )
            sync_result = handler.run_sync(
                issue_key="TJC-52",
                project="TJC",
                source="bug_created",
                run_id="run-img-soft-fail",
            )
            outcome = sync_result.outcome

    assert isinstance(outcome, TriageRecommendation)
    assert outcome.recommended_issue_type == "Story"
    img_events = [e for e in audit.events if isinstance(e, ImageContextExtractedAuditEvent)]
    assert img_events == []
    extraction = sync_result.image_extraction
    assert extraction is not None
    assert extraction.contexts == []
    assert extraction.attachments_considered == 0
    finish_img.assert_called_once_with()


@pytest.mark.unit
def test_noop_extractor_returns_empty_extraction_result() -> None:
    issue = FetchedIssue(
        issue_key="TJC-1",
        summary="s",
        description=None,
        issue_type="Bug",
        priority=None,
        reporter="r",
    )
    result = NoOpImageContextExtractor().extract(issue, run_id="r")
    assert result.contexts == []
    assert result.attachments_considered == 0
    assert result.attachments_extracted == 0
