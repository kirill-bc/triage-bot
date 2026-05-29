"""Unit tests for image context extraction contract."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from triage_service.adapters.image_context_extractor import (
    ImageContext,
    ImageContextExtractor,
    NoOpImageContextExtractor,
    OpenRouterVisionImageContextExtractor,
    _select_image_attachments,
)
from triage_service.adapters.jira_issue_fetcher import (
    AttachmentRef,
    FetchedIssue,
    JiraIssueFetchError,
)
from triage_service.adapters.openrouter_inference_client import (
    OpenRouterCompletionResult,
    OpenRouterInferenceError,
)
from triage_service.core.settings import AppSettings
from triage_service.observability.langfuse_inference_tracing import LangfuseInferenceTracer


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> AppSettings:
    monkeypatch.setenv("JIRA_API_KEY", "jira-api-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-token")
    monkeypatch.setenv("TRIAGE_WEBHOOK_TOKEN", "triage-token")
    return AppSettings()


@pytest.mark.unit
def test_select_image_attachments_keeps_unknown_size_before_known_large_when_capped() -> None:
    attachments = [
        AttachmentRef(
            id="huge",
            filename="huge.png",
            mime_type="image/png",
            size_bytes=9000,
            inline=True,
        ),
        AttachmentRef(
            id="unknown",
            filename="shot.png",
            mime_type="image/png",
            size_bytes=None,
            inline=True,
        ),
        AttachmentRef(
            id="small",
            filename="small.png",
            mime_type="image/png",
            size_bytes=50,
            inline=True,
        ),
    ]
    selected = _select_image_attachments(attachments, max_attachments=2)
    assert [ref.id for ref in selected] == ["unknown", "huge"]


@pytest.mark.unit
def test_image_context_model_fields() -> None:
    ctx = ImageContext(
        attachment_id="10001",
        filename="stacktrace.png",
        transcript="NullPointerException at line 42",
        summary="Red stack trace dialog.",
    )
    assert ctx.extraction_failure is None
    assert ctx.transcript is not None
    assert ctx.transcript.startswith("NullPointer")


@pytest.mark.unit
def test_noop_extractor_returns_empty_result() -> None:
    issue = FetchedIssue(
        issue_key="TJC-1",
        summary="s",
        issue_type="Bug",
        reporter="r",
        attachments=[
            AttachmentRef(
                id="10001",
                filename="a.png",
                mime_type="image/png",
                size_bytes=1024,
                inline=True,
            ),
        ],
    )
    extractor = NoOpImageContextExtractor()
    result = extractor.extract(issue, run_id="run-1")
    assert result.contexts == []
    assert result.attachments_considered == 0


@pytest.mark.unit
def test_image_context_extractor_protocol_accepts_noop_implementation() -> None:
    extractor: ImageContextExtractor = NoOpImageContextExtractor()
    assert hasattr(extractor, "extract")
    assert callable(extractor.extract)


def _vision_response(transcript: str, summary: str) -> str:
    return f"TRANSCRIPT:\n{transcript}\n\nSUMMARY:\n{summary}"


@pytest.fixture
def vision_extractor_deps() -> tuple[MagicMock, MagicMock]:
    jira_fetcher = MagicMock()
    inference = MagicMock()
    return jira_fetcher, inference


@pytest.mark.unit
def test_vision_extractor_uses_attachment_mime_not_sniffed_bytes(
    settings: AppSettings,
    vision_extractor_deps: tuple[MagicMock, MagicMock],
) -> None:
    jira_fetcher, inference = vision_extractor_deps
    jira_fetcher.fetch_attachment_bytes.return_value = b"\x89PNG\r\n\x1a\n"
    inference.effective_model_id = "google/gemini-2.0-flash-001"
    inference.chat_completion_with_details.return_value = OpenRouterCompletionResult(
        content=_vision_response("Error 500", "Red toast."),
    )
    issue = FetchedIssue(
        issue_key="TJC-mime",
        summary="s",
        issue_type="Bug",
        reporter="r",
        attachments=[
            AttachmentRef(
                id="10001",
                filename="toast.png",
                mime_type="image/jpeg",
                size_bytes=2048,
                inline=True,
            ),
        ],
    )
    extractor = OpenRouterVisionImageContextExtractor(
        settings=settings,
        jira_fetcher=jira_fetcher,
        inference_client=inference,
    )
    extractor.extract(issue, run_id="run-mime")
    call_args = inference.chat_completion_with_details.call_args[0][0]
    image_part = call_args[1]["content"][1]
    assert image_part["image_url"]["url"].startswith("data:image/jpeg;base64,")


@pytest.mark.unit
def test_vision_extractor_records_langfuse_generation_per_attachment(
    settings: AppSettings,
    vision_extractor_deps: tuple[MagicMock, MagicMock],
) -> None:
    jira_fetcher, inference = vision_extractor_deps
    jira_fetcher.fetch_attachment_bytes.return_value = b"\x89PNG\r\n\x1a\n"
    inference.effective_model_id = "google/gemini-2.0-flash-001"
    inference.chat_completion_with_details.return_value = OpenRouterCompletionResult(
        content=_vision_response("Error 500", "Red toast."),
        usage_details={"prompt_tokens": 50, "completion_tokens": 10},
        cost_details={"cost": 0.001},
    )
    issue = FetchedIssue(
        issue_key="TJC-trace",
        summary="s",
        issue_type="Bug",
        reporter="r",
        attachments=[
            AttachmentRef(
                id="10001",
                filename="toast.png",
                mime_type="image/png",
                size_bytes=2048,
                inline=True,
            ),
        ],
    )
    mock_tracer = MagicMock(spec=LangfuseInferenceTracer)
    finish_vision = MagicMock()
    vision_cm = MagicMock()
    vision_cm.__enter__ = MagicMock(return_value=finish_vision)
    vision_cm.__exit__ = MagicMock(return_value=False)
    mock_tracer.vision_generation.return_value = vision_cm

    extractor = OpenRouterVisionImageContextExtractor(
        settings=settings,
        jira_fetcher=jira_fetcher,
        inference_client=inference,
        inference_tracer=mock_tracer,
    )
    extractor.extract(issue, run_id="run-trace")

    mock_tracer.vision_generation.assert_called_once()
    call_kwargs = mock_tracer.vision_generation.call_args.kwargs
    assert call_kwargs["model"] == "google/gemini-2.0-flash-001"
    assert call_kwargs["attachment_id"] == "10001"
    assert call_kwargs["filename"] == "toast.png"
    assert call_kwargs["model_parameters"] == {"temperature": 0.0}
    finish_vision.assert_called_once()
    finish_args, finish_kwargs = finish_vision.call_args
    assert "TRANSCRIPT:" in finish_args[0]
    assert finish_kwargs["usage_details"]["prompt_tokens"] == 50
    assert finish_kwargs["cost_details"]["cost"] == 0.001


@pytest.mark.unit
def test_vision_extractor_returns_transcript_and_summary_on_success(
    settings: AppSettings,
    vision_extractor_deps: tuple[MagicMock, MagicMock],
) -> None:
    jira_fetcher, inference = vision_extractor_deps
    jira_fetcher.fetch_attachment_bytes.return_value = b"\x89PNG\r\n\x1a\n"
    inference.chat_completion_with_details.return_value = OpenRouterCompletionResult(
        content=_vision_response("Error 500", "A red toast shows an internal server error."),
    )
    issue = FetchedIssue(
        issue_key="TJC-2",
        summary="s",
        issue_type="Bug",
        reporter="r",
        attachments=[
            AttachmentRef(
                id="10001",
                filename="toast.png",
                mime_type="image/png",
                size_bytes=2048,
                inline=True,
            ),
        ],
    )
    extractor = OpenRouterVisionImageContextExtractor(
        settings=settings,
        jira_fetcher=jira_fetcher,
        inference_client=inference,
        max_attachments=5,
        max_bytes_per_image=1_000_000,
    )
    result = extractor.extract(issue, run_id="run-1")
    assert result.attachments_considered == 1
    assert result.attachments_extracted == 1
    assert len(result.contexts) == 1
    ctx = result.contexts[0]
    assert ctx.attachment_id == "10001"
    assert ctx.filename == "toast.png"
    assert ctx.extraction_failure is None
    assert ctx.transcript == "Error 500"
    assert "internal server error" in (ctx.summary or "")
    inference.chat_completion_with_details.assert_called_once()
    jira_fetcher.fetch_attachment_bytes.assert_called_once_with("10001", run_id="run-1")


@pytest.mark.unit
def test_vision_extractor_uses_composed_prompts(
    settings: AppSettings,
    vision_extractor_deps: tuple[MagicMock, MagicMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jira_fetcher, inference = vision_extractor_deps
    jira_fetcher.fetch_attachment_bytes.return_value = b"\x89PNG\r\n\x1a\n"
    inference.chat_completion_with_details.return_value = OpenRouterCompletionResult(
        content=_vision_response("(none)", "Blank modal."),
    )
    monkeypatch.setattr(
        "triage_service.adapters.image_context_extractor.compose_vision_system_prompt",
        lambda *, settings=None: "CUSTOM SYSTEM",
    )
    monkeypatch.setattr(
        "triage_service.adapters.image_context_extractor.compose_vision_user_instruction",
        lambda issue, *, settings=None: f"CUSTOM USER for {issue.issue_key}",
    )
    issue = FetchedIssue(
        issue_key="TJC-10",
        summary="s",
        issue_type="Bug",
        reporter="r",
        attachments=[
            AttachmentRef(
                id="att-1",
                filename="screen.png",
                mime_type="image/png",
                size_bytes=100,
                inline=True,
            ),
        ],
    )
    extractor = OpenRouterVisionImageContextExtractor(
        settings=settings,
        jira_fetcher=jira_fetcher,
        inference_client=inference,
        max_attachments=5,
        max_bytes_per_image=1_000_000,
    )
    extractor.extract(issue, run_id="run-prompts")
    messages = inference.chat_completion_with_details.call_args.args[0]
    assert messages[0]["content"] == "CUSTOM SYSTEM"
    assert messages[1]["content"][0]["text"] == "CUSTOM USER for TJC-10"


@pytest.mark.unit
def test_vision_extractor_user_message_includes_issue_context(
    settings: AppSettings,
    vision_extractor_deps: tuple[MagicMock, MagicMock],
) -> None:
    jira_fetcher, inference = vision_extractor_deps
    jira_fetcher.fetch_attachment_bytes.return_value = b"\x89PNG\r\n\x1a\n"
    inference.chat_completion_with_details.return_value = OpenRouterCompletionResult(
        content=_vision_response("Error 500", "Red toast."),
    )
    issue = FetchedIssue(
        issue_key="TJC-ctx",
        summary="Payment screen shows internal error",
        description="Screenshot attached below the steps.",
        reproduction_steps="Click Pay",
        issue_type="Bug",
        priority="P1",
        reporter="bob",
        attachments=[
            AttachmentRef(
                id="att-ctx",
                filename="toast.png",
                mime_type="image/png",
                size_bytes=100,
                inline=True,
            ),
        ],
    )
    extractor = OpenRouterVisionImageContextExtractor(
        settings=settings,
        jira_fetcher=jira_fetcher,
        inference_client=inference,
        max_attachments=5,
        max_bytes_per_image=1_000_000,
    )
    extractor.extract(issue, run_id="run-ctx")
    messages = inference.chat_completion_with_details.call_args.args[0]
    user_text = messages[1]["content"][0]["text"]
    assert "TJC-ctx" in user_text
    assert "Payment screen shows internal error" in user_text
    assert "Click Pay" in user_text


@pytest.mark.unit
def test_vision_extractor_sends_multimodal_message_with_data_url(
    settings: AppSettings,
    vision_extractor_deps: tuple[MagicMock, MagicMock],
) -> None:
    jira_fetcher, inference = vision_extractor_deps
    image_bytes = b"\x89PNG\r\n\x1a\n"
    jira_fetcher.fetch_attachment_bytes.return_value = image_bytes
    inference.chat_completion_with_details.return_value = OpenRouterCompletionResult(
        content=_vision_response("(none)", "Blank modal."),
    )
    issue = FetchedIssue(
        issue_key="TJC-3",
        summary="s",
        issue_type="Bug",
        reporter="r",
        attachments=[
            AttachmentRef(
                id="att-1",
                filename="screen.png",
                mime_type="image/png",
                size_bytes=100,
                inline=True,
            ),
        ],
    )
    extractor = OpenRouterVisionImageContextExtractor(
        settings=settings,
        jira_fetcher=jira_fetcher,
        inference_client=inference,
        max_attachments=5,
        max_bytes_per_image=1_000_000,
    )
    extractor.extract(issue, run_id="run-2")
    _args, kwargs = inference.chat_completion_with_details.call_args
    messages = _args[0]
    assert messages[0]["role"] == "system"
    user = messages[1]
    assert user["role"] == "user"
    content = user["content"]
    assert isinstance(content, list)
    image_part = next(part for part in content if part.get("type") == "image_url")
    url = image_part["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")


@pytest.mark.unit
def test_select_image_attachments_prioritizes_description_then_comment_refs(
    settings: AppSettings,
    vision_extractor_deps: tuple[MagicMock, MagicMock],
) -> None:
    jira_fetcher, inference = vision_extractor_deps
    jira_fetcher.fetch_attachment_bytes.return_value = b"\x89PNG\r\n\x1a\n"
    inference.chat_completion_with_details.return_value = OpenRouterCompletionResult(
        content=_vision_response("t", "s"),
    )
    issue = FetchedIssue(
        issue_key="TJC-4",
        summary="s",
        issue_type="Bug",
        reporter="r",
        attachments=[
            AttachmentRef(
                id="comment-large",
                filename="large.png",
                mime_type="image/png",
                size_bytes=9000,
                inline=False,
                referenced_in_comments=True,
            ),
            AttachmentRef(
                id="desc-small",
                filename="desc.png",
                mime_type="image/png",
                size_bytes=50,
                inline=True,
            ),
            AttachmentRef(
                id="comment-small",
                filename="comment-small.png",
                mime_type="image/png",
                size_bytes=40,
                inline=False,
                referenced_in_comments=True,
            ),
        ],
    )
    extractor = OpenRouterVisionImageContextExtractor(
        settings=settings,
        jira_fetcher=jira_fetcher,
        inference_client=inference,
        max_attachments=2,
        max_bytes_per_image=1_000_000,
    )
    result = extractor.extract(issue, run_id="run-3")
    assert [ctx.attachment_id for ctx in result.contexts] == ["desc-small", "comment-large"]
    assert inference.chat_completion_with_details.call_count == 2


@pytest.mark.unit
def test_vision_extractor_ignores_unreferenced_attachments(
    settings: AppSettings,
    vision_extractor_deps: tuple[MagicMock, MagicMock],
) -> None:
    jira_fetcher, inference = vision_extractor_deps
    jira_fetcher.fetch_attachment_bytes.return_value = b"\x89PNG\r\n\x1a\n"
    inference.chat_completion_with_details.return_value = OpenRouterCompletionResult(
        content=_vision_response("t", "s"),
    )
    issue = FetchedIssue(
        issue_key="TJC-4",
        summary="s",
        issue_type="Bug",
        reporter="r",
        attachments=[
            AttachmentRef(
                id="small-off",
                filename="small.png",
                mime_type="image/png",
                size_bytes=100,
                inline=False,
                referenced_in_comments=False,
            ),
            AttachmentRef(
                id="large-off",
                filename="large.png",
                mime_type="image/png",
                size_bytes=9000,
                inline=False,
                referenced_in_comments=False,
            ),
            AttachmentRef(
                id="inline-small",
                filename="inline.png",
                mime_type="image/png",
                size_bytes=50,
                inline=True,
            ),
        ],
    )
    extractor = OpenRouterVisionImageContextExtractor(
        settings=settings,
        jira_fetcher=jira_fetcher,
        inference_client=inference,
        max_attachments=2,
        max_bytes_per_image=1_000_000,
    )
    result = extractor.extract(issue, run_id="run-3")
    contexts = result.contexts
    assert [c.attachment_id for c in contexts] == ["inline-small"]
    assert inference.chat_completion_with_details.call_count == 1


@pytest.mark.unit
def test_vision_extractor_skips_non_image_attachments(
    settings: AppSettings,
    vision_extractor_deps: tuple[MagicMock, MagicMock],
) -> None:
    jira_fetcher, inference = vision_extractor_deps
    issue = FetchedIssue(
        issue_key="TJC-5",
        summary="s",
        issue_type="Bug",
        reporter="r",
        attachments=[
            AttachmentRef(
                id="log-1",
                filename="app.log",
                mime_type="text/plain",
                size_bytes=500,
                inline=True,
            ),
            AttachmentRef(
                id="img-1",
                filename="shot.png",
                mime_type="image/png",
                size_bytes=500,
                inline=True,
            ),
        ],
    )
    jira_fetcher.fetch_attachment_bytes.return_value = b"\x89PNG\r\n\x1a\n"
    inference.chat_completion_with_details.return_value = OpenRouterCompletionResult(
        content=_vision_response("x", "y"),
    )
    extractor = OpenRouterVisionImageContextExtractor(
        settings=settings,
        jira_fetcher=jira_fetcher,
        inference_client=inference,
        max_attachments=5,
        max_bytes_per_image=1_000_000,
    )
    result = extractor.extract(issue, run_id="run-4")
    contexts = result.contexts
    assert len(contexts) == 1
    assert contexts[0].attachment_id == "img-1"


@pytest.mark.unit
def test_vision_extractor_degrades_on_jira_fetch_failure(
    settings: AppSettings,
    vision_extractor_deps: tuple[MagicMock, MagicMock],
) -> None:
    jira_fetcher, inference = vision_extractor_deps
    jira_fetcher.fetch_attachment_bytes.side_effect = JiraIssueFetchError(
        "Jira request failed with HTTP 404",
        http_status=404,
    )
    issue = FetchedIssue(
        issue_key="TJC-6",
        summary="s",
        issue_type="Bug",
        reporter="r",
        attachments=[
            AttachmentRef(
                id="missing",
                filename="gone.png",
                mime_type="image/png",
                size_bytes=10,
                inline=True,
            ),
        ],
    )
    extractor = OpenRouterVisionImageContextExtractor(
        settings=settings,
        jira_fetcher=jira_fetcher,
        inference_client=inference,
        max_attachments=5,
        max_bytes_per_image=1_000_000,
    )
    result = extractor.extract(issue, run_id="run-5")
    contexts = result.contexts
    assert len(contexts) == 1
    assert contexts[0].extraction_failure is not None
    assert "404" in contexts[0].extraction_failure
    assert contexts[0].transcript is None
    inference.chat_completion_with_details.assert_not_called()


@pytest.mark.unit
def test_vision_extractor_degrades_when_attachment_bytes_are_html(
    settings: AppSettings,
    vision_extractor_deps: tuple[MagicMock, MagicMock],
) -> None:
    jira_fetcher, inference = vision_extractor_deps
    jira_fetcher.fetch_attachment_bytes.return_value = b"<!DOCTYPE html><html>"
    issue = FetchedIssue(
        issue_key="TJC-7b",
        summary="s",
        issue_type="Bug",
        reporter="r",
        attachments=[
            AttachmentRef(
                id="html",
                filename="fake.png",
                mime_type="image/png",
                size_bytes=20,
                inline=True,
            ),
        ],
    )
    extractor = OpenRouterVisionImageContextExtractor(
        settings=settings,
        jira_fetcher=jira_fetcher,
        inference_client=inference,
        max_attachments=5,
        max_bytes_per_image=1_000_000,
    )
    result = extractor.extract(issue, run_id="run-html")
    contexts = result.contexts
    assert "not image binary" in (contexts[0].extraction_failure or "")
    inference.chat_completion_with_details.assert_not_called()


@pytest.mark.unit
def test_vision_extractor_degrades_on_oversize_image(
    settings: AppSettings,
    vision_extractor_deps: tuple[MagicMock, MagicMock],
) -> None:
    jira_fetcher, inference = vision_extractor_deps
    jira_fetcher.fetch_attachment_bytes.return_value = b"x" * 20
    issue = FetchedIssue(
        issue_key="TJC-7",
        summary="s",
        issue_type="Bug",
        reporter="r",
        attachments=[
            AttachmentRef(
                id="big",
                filename="huge.png",
                mime_type="image/png",
                size_bytes=20,
                inline=True,
            ),
        ],
    )
    extractor = OpenRouterVisionImageContextExtractor(
        settings=settings,
        jira_fetcher=jira_fetcher,
        inference_client=inference,
        max_attachments=5,
        max_bytes_per_image=10,
    )
    result = extractor.extract(issue, run_id="run-6")
    contexts = result.contexts
    assert contexts[0].extraction_failure is not None
    assert "size" in contexts[0].extraction_failure.lower()
    inference.chat_completion_with_details.assert_not_called()


@pytest.mark.unit
def test_vision_extractor_degrades_on_vision_api_failure(
    settings: AppSettings,
    vision_extractor_deps: tuple[MagicMock, MagicMock],
) -> None:
    jira_fetcher, inference = vision_extractor_deps
    jira_fetcher.fetch_attachment_bytes.return_value = b"\x89PNG\r\n\x1a\n"
    inference.chat_completion_with_details.side_effect = OpenRouterInferenceError(
        "OpenRouter request failed with HTTP 503",
        http_status=503,
    )
    issue = FetchedIssue(
        issue_key="TJC-8",
        summary="s",
        issue_type="Bug",
        reporter="r",
        attachments=[
            AttachmentRef(
                id="img",
                filename="a.png",
                mime_type="image/png",
                size_bytes=10,
                inline=True,
            ),
        ],
    )
    extractor = OpenRouterVisionImageContextExtractor(
        settings=settings,
        jira_fetcher=jira_fetcher,
        inference_client=inference,
        max_attachments=5,
        max_bytes_per_image=1_000_000,
    )
    result = extractor.extract(issue, run_id="run-7")
    contexts = result.contexts
    assert contexts[0].extraction_failure is not None
    assert "503" in contexts[0].extraction_failure


@pytest.mark.unit
def test_vision_extractor_never_raises_when_one_of_two_images_fails(
    settings: AppSettings,
    vision_extractor_deps: tuple[MagicMock, MagicMock],
) -> None:
    jira_fetcher, inference = vision_extractor_deps

    def fetch_side_effect(attachment_id: str, *, run_id: str) -> bytes:
        _ = run_id
        if attachment_id == "bad":
            raise JiraIssueFetchError("HTTP 500", http_status=500)
        return b"\x89PNG\r\n\x1a\n"

    jira_fetcher.fetch_attachment_bytes.side_effect = fetch_side_effect
    inference.chat_completion_with_details.return_value = OpenRouterCompletionResult(
        content=_vision_response("ok text", "ok summary"),
    )
    issue = FetchedIssue(
        issue_key="TJC-9",
        summary="s",
        issue_type="Bug",
        reporter="r",
        attachments=[
            AttachmentRef(
                id="bad",
                filename="bad.png",
                mime_type="image/png",
                size_bytes=10,
                inline=True,
            ),
            AttachmentRef(
                id="good",
                filename="good.png",
                mime_type="image/png",
                size_bytes=10,
                inline=True,
            ),
        ],
    )
    extractor = OpenRouterVisionImageContextExtractor(
        settings=settings,
        jira_fetcher=jira_fetcher,
        inference_client=inference,
        max_attachments=5,
        max_bytes_per_image=1_000_000,
    )
    result = extractor.extract(issue, run_id="run-8")
    contexts = result.contexts
    assert len(contexts) == 2
    by_id = {c.attachment_id: c for c in contexts}
    assert by_id["bad"].extraction_failure is not None
    assert by_id["good"].transcript == "ok text"
