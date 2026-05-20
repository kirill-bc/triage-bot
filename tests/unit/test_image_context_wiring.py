"""Unit tests for image-context settings, factory, and triage handler wiring."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import httpx
import pytest
from pydantic import ValidationError

from triage_service.adapters.image_context_extractor import (
    ImageContext,
    ImageContextExtractionResult,
    NoOpImageContextExtractor,
    OpenRouterVisionImageContextExtractor,
    build_image_context_extractor,
)
from triage_service.adapters.jira_issue_fetcher import (
    AttachmentRef,
    FetchedIssue,
    JiraIssueFetcher,
)
from triage_service.adapters.openrouter_inference_client import OpenRouterInferenceClient
from triage_service.core.settings import AppSettings
from triage_service.core.triage_handler import TriageHandler
from triage_service.core.policy_context import PolicyContext


def _app_settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> AppSettings:
    monkeypatch.setenv("JIRA_API_KEY", "jira-api-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-token")
    monkeypatch.setenv("TRIAGE_WEBHOOK_TOKEN", "triage-token")
    monkeypatch.setenv("JIRA_CLOUD_ID", "cloud-id-test")
    monkeypatch.setenv("JIRA_USER_EMAIL", "bot@example.com")
    if "TRIAGE_IMAGE_CONTEXT_ENABLED" not in env:
        monkeypatch.setenv("TRIAGE_IMAGE_CONTEXT_ENABLED", "false")
    if "TRIAGE_TEXT_MODEL" not in env:
        monkeypatch.setenv("TRIAGE_TEXT_MODEL", "openai/gpt-4o-mini")
    if "TRIAGE_VISION_MODEL" not in env:
        monkeypatch.setenv("TRIAGE_VISION_MODEL", "google/gemini-2.0-flash-001")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return AppSettings()


@pytest.mark.unit
def test_settings_image_context_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _app_settings(monkeypatch)
    assert settings.triage_image_context_enabled is False
    assert settings.triage_vision_model == "google/gemini-2.0-flash-001"
    assert settings.triage_image_context_max_attachments == 5
    assert settings.triage_image_context_max_bytes_per_image == 5 * 1024 * 1024
    assert settings.triage_image_context_timeout_seconds == 90.0
    assert settings.triage_audit_redact_image_transcript is True
    assert settings.langfuse_prompt_management_enabled is False


@pytest.mark.unit
def test_settings_rejects_image_context_max_bytes_per_image_above_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValidationError):
        _app_settings(
            monkeypatch,
            TRIAGE_IMAGE_CONTEXT_MAX_BYTES_PER_IMAGE=str(21 * 1024 * 1024),
        )


@pytest.mark.unit
def test_build_image_context_extractor_noop_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _app_settings(monkeypatch, TRIAGE_IMAGE_CONTEXT_ENABLED="false")
    fetcher = MagicMock(spec=JiraIssueFetcher)
    extractor = build_image_context_extractor(settings, fetcher)
    assert isinstance(extractor, NoOpImageContextExtractor)


@pytest.mark.unit
def test_build_image_context_extractor_vision_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _app_settings(
        monkeypatch,
        TRIAGE_IMAGE_CONTEXT_ENABLED="true",
        TRIAGE_VISION_MODEL="openai/gpt-4o",
    )
    fetcher = MagicMock(spec=JiraIssueFetcher)
    extractor = build_image_context_extractor(settings, fetcher)
    assert isinstance(extractor, OpenRouterVisionImageContextExtractor)
    assert extractor._inference.effective_model_id == "openai/gpt-4o"


class _NoOpExecutor:
    def apply_triage_outcome(self, **kwargs: object) -> None:
        _ = kwargs


def _jira_payload_for(issue: FetchedIssue) -> dict[str, object]:
    return {
        "key": issue.issue_key,
        "fields": {
            "summary": issue.summary,
            "description": issue.description,
            "issuetype": {"name": issue.issue_type},
            "priority": {"name": issue.priority} if issue.priority else None,
            "reporter": {"displayName": issue.reporter},
            "attachment": [
                {
                    "id": att.id,
                    "filename": att.filename,
                    "mimeType": att.mime_type,
                    "size": att.size_bytes,
                }
                for att in issue.attachments
            ],
        },
    }


@pytest.mark.unit
def test_run_sync_invokes_image_extractor_and_enriches_classification_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _app_settings(monkeypatch)
    issue = FetchedIssue(
        issue_key="TJC-100",
        summary="Crash",
        description="see screenshot",
        issue_type="Bug",
        priority="P2",
        reporter="r",
        attachments=[
            AttachmentRef(
                id="att-1",
                filename="error.png",
                mime_type="image/png",
                size_bytes=100,
                inline=True,
            ),
        ],
    )
    image_contexts = [
        ImageContext(
            attachment_id="att-1",
            filename="error.png",
            transcript="NullPointerException",
            summary="Red error dialog.",
        ),
    ]
    mock_extractor = MagicMock()
    mock_extractor.extract.return_value = ImageContextExtractionResult(
        contexts=image_contexts,
        attachments_considered=len(image_contexts),
        attachments_extracted=len(image_contexts),
    )

    story_json = '{"recommended_issue_type":"Story","confidence":0.8,"reason":"Docs."}'
    captured_user_messages: list[str] = []

    def jira_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_jira_payload_for(issue))

    def openrouter_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        for msg in body.get("messages", []):
            if msg.get("role") == "user" and isinstance(msg.get("content"), str):
                captured_user_messages.append(msg["content"])
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": story_json}}]},
        )

    with httpx.Client(transport=httpx.MockTransport(jira_handler)) as j_client:
        with httpx.Client(transport=httpx.MockTransport(openrouter_handler)) as o_client:
            fetcher = JiraIssueFetcher(settings, client=j_client)
            inference = OpenRouterInferenceClient(settings, client=o_client)
            handler = TriageHandler(
                allowed_projects=("TJC",),
                fetcher=fetcher,
                inference=inference,
                policy=PolicyContext(
                    bug_definition="bug policy",
                    priority_definition="priority policy",
                ),
                executor=_NoOpExecutor(),
                image_context_extractor=mock_extractor,
                settings=settings,
            )
            sync_result = handler.run_sync(
                issue_key="TJC-100",
                project="TJC",
                source="bug_created",
                run_id="run-img-1",
            )

    from triage_service.core.triage_recommendation_parser import TriageRecommendation

    outcome = sync_result.outcome
    assert isinstance(outcome, TriageRecommendation)
    assert outcome.recommended_issue_type == "Story"
    mock_extractor.extract.assert_called_once()
    extract_issue = mock_extractor.extract.call_args.args[0]
    assert extract_issue.issue_key == "TJC-100"
    assert mock_extractor.extract.call_args.kwargs["run_id"] == "run-img-1"
    assert captured_user_messages
    assert "Attached images:" in captured_user_messages[0]
    assert "Red error dialog." in captured_user_messages[0]
    assert "NullPointerException" not in captured_user_messages[0]


@pytest.mark.unit
def test_run_sync_on_fetched_skips_image_extractor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _app_settings(monkeypatch)
    issue = FetchedIssue(
        issue_key="TJC-101",
        summary="s",
        issue_type="Bug",
        reporter="r",
    )
    mock_extractor = MagicMock()
    story_json = '{"recommended_issue_type":"Story","confidence":0.5,"reason":"x"}'

    def openrouter_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": story_json}}]},
        )

    with httpx.Client(transport=httpx.MockTransport(openrouter_handler)) as o_client:
        inference = OpenRouterInferenceClient(settings, client=o_client)
        handler = TriageHandler(
            allowed_projects=("TJC",),
            fetcher=MagicMock(),
            inference=inference,
            policy=PolicyContext(bug_definition="b", priority_definition="p"),
            executor=_NoOpExecutor(),
            image_context_extractor=mock_extractor,
            settings=settings,
        )
        handler.run_sync_on_fetched(
            issue=issue,
            project="TJC",
            source="manual_trigger",
            run_id="run-img-2",
        )

    mock_extractor.extract.assert_not_called()


@pytest.mark.unit
def test_run_sync_on_fetched_uses_pre_extracted_image_contexts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _app_settings(monkeypatch)
    issue = FetchedIssue(
        issue_key="TJC-102",
        summary="s",
        issue_type="Bug",
        priority="P2",
        reporter="r",
        attachments=[
            AttachmentRef(
                id="att-1",
                filename="err.png",
                mime_type="image/png",
                inline=True,
            ),
        ],
    )
    mock_extractor = MagicMock()
    contexts = [
        ImageContext(
            attachment_id="att-1",
            filename="err.png",
            transcript="Error 500",
            summary="Server error toast",
        ),
    ]
    extraction = ImageContextExtractionResult(
        contexts=contexts,
        attachments_considered=1,
        attachments_extracted=1,
    )
    captured_user_messages: list[str] = []
    story_json = '{"recommended_issue_type":"Story","confidence":0.5,"reason":"x"}'

    def openrouter_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        captured_user_messages.append(body["messages"][1]["content"])
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": story_json}}]},
        )

    with httpx.Client(transport=httpx.MockTransport(openrouter_handler)) as o_client:
        inference = OpenRouterInferenceClient(settings, client=o_client)
        handler = TriageHandler(
            allowed_projects=("TJC",),
            fetcher=MagicMock(),
            inference=inference,
            policy=PolicyContext(bug_definition="b", priority_definition="p"),
            executor=_NoOpExecutor(),
            image_context_extractor=mock_extractor,
            settings=settings,
        )
        handler.run_sync_on_fetched(
            issue=issue,
            project="TJC",
            source="manual_trigger",
            run_id="run-img-3",
            image_contexts=contexts,
            image_extraction=extraction,
        )

    mock_extractor.extract.assert_not_called()
    assert captured_user_messages
    assert "Attached images:" in captured_user_messages[0]
    assert "Server error toast" in captured_user_messages[0]
    assert "Error 500" not in captured_user_messages[0]


@pytest.mark.unit
def test_run_sync_preprocesses_attachments_when_vision_extractor_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _app_settings(
        monkeypatch,
        TRIAGE_IMAGE_CONTEXT_ENABLED="true",
        TRIAGE_VISION_MODEL="openai/gpt-4o",
    )
    issue = FetchedIssue(
        issue_key="TJC-200",
        summary="UI broken",
        description="screenshot attached",
        issue_type="Bug",
        priority="P2",
        reporter="r",
        attachments=[
            AttachmentRef(
                id="att-vision",
                filename="ui.png",
                mime_type="image/png",
                size_bytes=64,
                inline=True,
            ),
        ],
    )
    vision_response = (
        "TRANSCRIPT:\n"
        "Button Save disabled\n\n"
        "SUMMARY:\n"
        "Greyed-out save button on settings form."
    )
    story_json = '{"recommended_issue_type":"Story","confidence":0.9,"reason":"UI polish."}'
    openrouter_calls: list[str] = []

    def jira_handler(request: httpx.Request) -> httpx.Response:
        if "attachment/content" in str(request.url):
            return httpx.Response(200, content=b"\x89PNG\r\n\x1a\n\x00\x00\x00\x00")
        return httpx.Response(
            200,
            json={
                "key": issue.issue_key,
                "fields": {
                    "summary": issue.summary,
                    "description": {
                        "type": "doc",
                        "version": 1,
                        "content": [
                            {
                                "type": "mediaSingle",
                                "content": [
                                    {
                                        "type": "media",
                                        "attrs": {"id": "att-vision"},
                                    },
                                ],
                            },
                        ],
                    },
                    "issuetype": {"name": issue.issue_type},
                    "priority": {"name": issue.priority},
                    "reporter": {"displayName": issue.reporter},
                    "attachment": [
                        {
                            "id": "att-vision",
                            "filename": "ui.png",
                            "mimeType": "image/png",
                            "size": 64,
                        },
                    ],
                },
            },
        )

    def openrouter_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        openrouter_calls.append(body["model"])
        content = body["messages"][-1].get("content")
        if isinstance(content, list):
            return httpx.Response(
                200,
                json={"choices": [{"message": {"role": "assistant", "content": vision_response}}]},
            )
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": story_json}}]},
        )

    with httpx.Client(transport=httpx.MockTransport(jira_handler)) as j_client:
        with httpx.Client(transport=httpx.MockTransport(openrouter_handler)) as o_client:
            fetcher = JiraIssueFetcher(settings, client=j_client)
            extractor = build_image_context_extractor(
                settings,
                fetcher,
                vision_client=OpenRouterInferenceClient(
                    settings,
                    client=o_client,
                    model_override=settings.triage_vision_model,
                ),
            )
            inference = OpenRouterInferenceClient(settings, client=o_client)
            handler = TriageHandler(
                allowed_projects=("TJC",),
                fetcher=fetcher,
                inference=inference,
                policy=PolicyContext(bug_definition="b", priority_definition="p"),
                executor=_NoOpExecutor(),
                image_context_extractor=extractor,
                settings=settings,
            )
            sync_result = handler.run_sync(
                issue_key="TJC-200",
                project="TJC",
                source="bug_created",
                run_id="run-preprocess",
            )

    assert openrouter_calls[0] == "openai/gpt-4o"
    assert openrouter_calls[-1] == settings.triage_text_model
    classification_calls = [
        c for c in openrouter_calls if c == settings.triage_text_model
    ]
    assert len(classification_calls) == 1
    last = sync_result.image_extraction
    assert last is not None
    assert last.attachments_considered == 1
    assert last.attachments_extracted == 1
    assert len(last.contexts) == 1
    assert last.contexts[0].transcript is not None
