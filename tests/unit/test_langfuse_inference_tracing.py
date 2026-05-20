"""Unit tests for LangFuse-backed inference step tracing."""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest

from triage_service.observability.langfuse_inference_tracing import (
    LangfuseInferenceTracer,
    build_langfuse_inference_tracer,
    langfuse_session_id,
    stable_langfuse_trace_id,
)


@pytest.mark.unit
def test_langfuse_inference_tracer_flush_calls_client_when_present() -> None:
    client = MagicMock()
    tracer = LangfuseInferenceTracer(client)
    tracer.flush()
    client.flush.assert_called_once()


@pytest.mark.unit
def test_langfuse_inference_tracer_flush_swallows_errors() -> None:
    client = MagicMock()
    client.flush.side_effect = RuntimeError("network")
    tracer = LangfuseInferenceTracer(client)
    tracer.flush()


@pytest.mark.unit
def test_stable_langfuse_trace_id_strips_hyphens_for_uuid_run_id() -> None:
    rid = "550e8400-e29b-41d4-a716-446655440000"
    assert stable_langfuse_trace_id(rid) == "550e8400e29b41d4a716446655440000"
    assert len(stable_langfuse_trace_id(rid)) == 32


@pytest.mark.unit
def test_langfuse_session_id_strips_whitespace() -> None:
    assert langfuse_session_id("  run-abc  ") == "run-abc"


@pytest.mark.unit
def test_langfuse_session_id_truncates_to_langfuse_limit() -> None:
    long_id = "x" * 250
    assert len(langfuse_session_id(long_id)) == 199


@pytest.mark.unit
def test_tracer_triage_run_session_propagates_session_id() -> None:
    client = MagicMock()
    tracer = LangfuseInferenceTracer(client)

    with patch(
        "triage_service.observability.langfuse_inference_tracing.propagate_attributes"
    ) as propagate:
        propagate.return_value.__enter__ = MagicMock(return_value=None)
        propagate.return_value.__exit__ = MagicMock(return_value=False)
        with tracer.triage_run_session(run_id="run-42"):
            pass

    propagate.assert_called_once_with(session_id="run-42")


@pytest.mark.unit
def test_tracer_triage_run_session_noop_without_client() -> None:
    tracer = LangfuseInferenceTracer(None)

    with patch(
        "triage_service.observability.langfuse_inference_tracing.propagate_attributes"
    ) as propagate:
        with tracer.triage_run_session(run_id="run-42"):
            pass

    propagate.assert_not_called()


@pytest.mark.unit
def test_stable_langfuse_trace_id_non_uuid_is_32_hex_chars() -> None:
    rid = "benchmark-run-20250514"
    out = stable_langfuse_trace_id(rid)
    assert len(out) == 32
    assert int(out, 16) >= 0


@pytest.mark.unit
def test_tracer_disabled_runs_without_client() -> None:
    tracer = LangfuseInferenceTracer(None)

    with tracer.triage_issue_trace(run_id="r1", issue_key="TJC-1", project="TJC"):
        with tracer.model_generation(
            step="classification",
            model="openai/gpt-4o-mini",
            messages=[{"role": "user", "content": "hi"}],
            model_parameters={"temperature": 0.2},
        ) as finish:
            finish("raw-out", {"parsed": {"recommended_issue_type": "Story"}})


@pytest.mark.unit
def test_tracer_records_root_span_and_classification_generation() -> None:
    root_cm = MagicMock()
    gen_cm = MagicMock()
    root_obs = MagicMock()
    gen_obs = MagicMock()
    root_cm.__enter__.return_value = root_obs
    gen_cm.__enter__.return_value = gen_obs

    @contextmanager
    def root_ctx(**kwargs: Any) -> Any:
        _ = kwargs
        yield root_obs

    @contextmanager
    def gen_ctx(**kwargs: Any) -> Any:
        _ = kwargs
        yield gen_obs

    client = MagicMock()
    client.start_as_current_observation.side_effect = [root_ctx(), gen_ctx()]

    tracer = LangfuseInferenceTracer(client, redact_model_input=False, redact_model_output=False)

    with tracer.triage_issue_trace(
        run_id="550e8400-e29b-41d4-a716-446655440000",
        issue_key="TJC-9",
        project="TJC",
    ):
        with tracer.model_generation(
            step="classification",
            model="anthropic/claude-3-haiku",
            messages=[{"role": "system", "content": "sys"}, {"role": "user", "content": "u"}],
            model_parameters={"temperature": 0.2, "max_tokens": 100},
        ) as finish:
            finish(
                '{"recommended_issue_type":"Bug","confidence":0.5,"reason":"because"}',
                {
                    "parsed": {
                        "recommended_issue_type": "Bug",
                        "confidence": 0.5,
                        "reason": "because",
                    },
                },
                usage_details={
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                },
                cost_details={"cost": 0.00123},
            )

    assert client.start_as_current_observation.call_count == 2
    first = client.start_as_current_observation.call_args_list[0]
    assert first == call(
        name="triage_issue_pipeline",
        as_type="span",
        metadata={
            "run_id": "550e8400-e29b-41d4-a716-446655440000",
            "issue_key": "TJC-9",
            "project": "TJC",
            "operation": "triage_issue",
        },
    )
    second = client.start_as_current_observation.call_args_list[1]
    assert second.kwargs["name"] == "inference_classification"
    assert second.kwargs["as_type"] == "generation"
    assert second.kwargs["model"] == "anthropic/claude-3-haiku"
    assert second.kwargs["input"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "u"},
    ]
    assert second.kwargs["model_parameters"] == {"temperature": 0.2, "max_tokens": 100}
    assert second.kwargs["metadata"] == {
        "operation": "inference_classification",
        "step": "classification",
    }
    gen_obs.update.assert_called_once()
    uargs, ukwargs = gen_obs.update.call_args
    assert not uargs
    assert ukwargs["output"] == (
        '{"recommended_issue_type":"Bug","confidence":0.5,"reason":"because"}'
    )
    assert ukwargs["metadata"]["parsed"]["recommended_issue_type"] == "Bug"
    assert ukwargs["usage_details"] == {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
    }
    assert ukwargs["cost_details"] == {"cost": 0.00123}


@pytest.mark.unit
def test_tracer_swallows_langfuse_errors_does_not_propagate() -> None:
    client = MagicMock()

    def boom(**kwargs: Any) -> Any:
        _ = kwargs
        raise RuntimeError("langfuse down")

    client.start_as_current_observation.side_effect = boom
    tracer = LangfuseInferenceTracer(client)

    with tracer.triage_issue_trace(
        run_id=str(uuid.uuid4()),
        issue_key="X-1",
        project="X",
    ):
        pass  # should not raise


@pytest.mark.unit
def test_tracer_finish_swallows_update_errors() -> None:
    gen_obs = MagicMock()
    gen_obs.update.side_effect = RuntimeError("update failed")

    @contextmanager
    def gen_ctx(**kwargs: Any) -> Any:
        _ = kwargs
        yield gen_obs

    @contextmanager
    def root_ctx(**kwargs: Any) -> Any:
        _ = kwargs
        yield MagicMock()

    client = MagicMock()
    client.start_as_current_observation.side_effect = [root_ctx(), gen_ctx()]
    tracer = LangfuseInferenceTracer(client)

    with tracer.triage_issue_trace(run_id=str(uuid.uuid4()), issue_key="A-1", project="A"):
        with tracer.model_generation(
            step="priority",
            model="m",
            messages=[{"role": "user", "content": "x"}],
            model_parameters={"temperature": 0.1},
        ) as finish:
            finish("raw", {"parsed": {"recommended_priority": "P1"}})


@pytest.mark.unit
def test_tracer_redacts_generation_input_and_output_when_redaction_enabled() -> None:
    gen_obs = MagicMock()

    @contextmanager
    def gen_ctx(**kwargs: Any) -> Any:
        _ = kwargs
        yield gen_obs

    @contextmanager
    def root_ctx(**kwargs: Any) -> Any:
        _ = kwargs
        yield MagicMock()

    client = MagicMock()
    client.start_as_current_observation.side_effect = [root_ctx(), gen_ctx()]
    tracer = LangfuseInferenceTracer(
        client,
        redact_model_input=True,
        redact_model_output=True,
    )
    story_out = '{"recommended_issue_type":"Story"}'

    with tracer.triage_issue_trace(run_id=str(uuid.uuid4()), issue_key="A-1", project="A"):
        with tracer.model_generation(
            step="classification",
            model="m",
            messages=[{"role": "user", "content": "secret-prompt"}],
            model_parameters={"temperature": 0.1},
        ) as finish:
            finish(story_out, {"parsed": {"recommended_issue_type": "Story"}})

    gen_call = client.start_as_current_observation.call_args_list[1]
    in_messages = gen_call.kwargs["input"]
    assert len(in_messages) == 1
    assert in_messages[0]["role"] == "user"
    assert in_messages[0]["content"].startswith("[REDACTED] len=")
    assert 'preview="secret-prompt"' in in_messages[0]["content"]
    gen_obs.update.assert_called_once()
    ukwargs = gen_obs.update.call_args.kwargs
    assert ukwargs["output"].startswith("[REDACTED] len=")
    assert ukwargs["metadata"]["redacted_output"] is True
    assert ukwargs["metadata"]["output_length"] == len(story_out)


@pytest.mark.unit
def test_tracer_truncates_oversized_generation_output_and_metadata() -> None:
    gen_obs = MagicMock()

    @contextmanager
    def gen_ctx(**kwargs: Any) -> Any:
        _ = kwargs
        yield gen_obs

    @contextmanager
    def root_ctx(**kwargs: Any) -> Any:
        _ = kwargs
        yield MagicMock()

    client = MagicMock()
    client.start_as_current_observation.side_effect = [root_ctx(), gen_ctx()]
    tracer = LangfuseInferenceTracer(client, redact_model_input=False, redact_model_output=False)
    huge = "H" * 9000
    huge_reason = "R" * 9000

    with tracer.triage_issue_trace(run_id=str(uuid.uuid4()), issue_key="A-1", project="A"):
        with tracer.model_generation(
            step="classification",
            model="m",
            messages=[{"role": "user", "content": "ok"}],
            model_parameters={"temperature": 0.1},
        ) as finish:
            finish(huge, {"parsed": {"recommended_issue_type": "Bug", "reason": huge_reason}})

    ukwargs = gen_obs.update.call_args.kwargs
    assert ukwargs["metadata"]["log_payload_truncated"] is True
    assert len(ukwargs["output"]) < len(huge)
    assert "truncated" in ukwargs["output"]
    assert len(ukwargs["metadata"]["parsed"]["reason"]) < len(huge_reason)


@pytest.mark.unit
def test_tracer_marks_truncation_on_oversized_generation_input() -> None:
    gen_obs = MagicMock()

    @contextmanager
    def gen_ctx(**kwargs: Any) -> Any:
        _ = kwargs
        yield gen_obs

    @contextmanager
    def root_ctx(**kwargs: Any) -> Any:
        _ = kwargs
        yield MagicMock()

    client = MagicMock()
    client.start_as_current_observation.side_effect = [root_ctx(), gen_ctx()]
    tracer = LangfuseInferenceTracer(client, redact_model_input=False, redact_model_output=False)
    long_content = "C" * 9000

    with tracer.triage_issue_trace(run_id=str(uuid.uuid4()), issue_key="A-1", project="A"):
        with tracer.model_generation(
            step="priority",
            model="m",
            messages=[{"role": "user", "content": long_content}],
            model_parameters={"temperature": 0.1},
        ) as finish:
            finish("small", {"parsed": {"recommended_priority": "P1"}})

    gen_call = client.start_as_current_observation.call_args_list[1]
    assert gen_call.kwargs["metadata"]["log_payload_truncated"] is True
    assert len(gen_call.kwargs["input"][0]["content"]) < len(long_content)
    assert "truncated" in gen_call.kwargs["input"][0]["content"]


@pytest.mark.unit
def test_model_generation_uses_explicit_trace_context_when_available() -> None:
    gen_obs = MagicMock()

    @contextmanager
    def gen_ctx(**kwargs: Any) -> Any:
        _ = kwargs
        yield gen_obs

    @contextmanager
    def root_ctx(**kwargs: Any) -> Any:
        _ = kwargs
        yield MagicMock()

    client = MagicMock()
    client.get_current_trace_id.return_value = "a" * 32
    client.get_current_observation_id.return_value = "b" * 16
    client.start_as_current_observation.side_effect = [root_ctx(), gen_ctx()]
    tracer = LangfuseInferenceTracer(client)

    with tracer.triage_issue_trace(run_id=str(uuid.uuid4()), issue_key="A-1", project="A"):
        with tracer.model_generation(
            step="priority",
            model="m",
            messages=[{"role": "user", "content": "x"}],
            model_parameters={"temperature": 0.1},
        ) as finish:
            finish("raw", {"parsed": {"recommended_priority": "P1"}})

    gen_call = client.start_as_current_observation.call_args_list[1]
    assert gen_call.kwargs["trace_context"] == {
        "trace_id": "a" * 32,
        "parent_span_id": "b" * 16,
    }


@pytest.mark.unit
def test_model_generation_omits_trace_context_when_unavailable() -> None:
    gen_obs = MagicMock()

    @contextmanager
    def gen_ctx(**kwargs: Any) -> Any:
        _ = kwargs
        yield gen_obs

    @contextmanager
    def root_ctx(**kwargs: Any) -> Any:
        _ = kwargs
        yield MagicMock()

    client = MagicMock()
    client.get_current_trace_id.return_value = "a" * 32
    client.get_current_observation_id.return_value = None
    client.start_as_current_observation.side_effect = [root_ctx(), gen_ctx()]
    tracer = LangfuseInferenceTracer(client)

    with tracer.triage_issue_trace(run_id=str(uuid.uuid4()), issue_key="A-1", project="A"):
        with tracer.model_generation(
            step="priority",
            model="m",
            messages=[{"role": "user", "content": "x"}],
            model_parameters={"temperature": 0.1},
        ) as finish:
            finish("raw", {"parsed": {"recommended_priority": "P1"}})

    gen_call = client.start_as_current_observation.call_args_list[1]
    assert "trace_context" not in gen_call.kwargs


@pytest.mark.unit
def test_build_langfuse_inference_tracer_noop_without_keys() -> None:
    tracer = build_langfuse_inference_tracer(public_key=None, secret_key=None)
    assert tracer._client is None
    tracer_partial = build_langfuse_inference_tracer(public_key="pk", secret_key="  ")
    assert tracer_partial._client is None


@pytest.mark.unit
def test_tracer_vision_generation_keeps_image_when_redact_input_false() -> None:
    gen_obs = MagicMock()

    @contextmanager
    def gen_ctx(**kwargs: Any) -> Any:
        _ = kwargs
        yield gen_obs

    client = MagicMock()
    client.start_as_current_observation.side_effect = [gen_ctx()]
    tracer = LangfuseInferenceTracer(client, redact_model_input=False, redact_model_output=False)
    huge_b64 = "B" * 20_000
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": "vision system"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "user instruction"},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{huge_b64}"},
                },
            ],
        },
    ]

    with tracer.vision_generation(
        model="m",
        messages=messages,
        model_parameters={"temperature": 0.0},
        attachment_id="att-1",
        filename="screen.png",
    ) as finish:
        finish("ok", {})

    gen_call = client.start_as_current_observation.call_args
    traced = gen_call.kwargs["input"]
    image_url = traced[1]["content"][1]["image_url"]["url"]
    assert huge_b64 in image_url
    assert "\u2026" not in image_url
    assert gen_call.kwargs["metadata"].get("log_payload_truncated") is not True


@pytest.mark.unit
def test_tracer_records_vision_generation_with_redacted_image_payload() -> None:
    root_cm = MagicMock()
    img_cm = MagicMock()
    gen_cm = MagicMock()
    root_obs = MagicMock()
    img_obs = MagicMock()
    gen_obs = MagicMock()
    root_cm.__enter__.return_value = root_obs
    img_cm.__enter__.return_value = img_obs
    gen_cm.__enter__.return_value = gen_obs

    @contextmanager
    def root_ctx(**kwargs: Any) -> Any:
        _ = kwargs
        yield root_obs

    @contextmanager
    def img_ctx(**kwargs: Any) -> Any:
        _ = kwargs
        yield img_obs

    @contextmanager
    def gen_ctx(**kwargs: Any) -> Any:
        _ = kwargs
        yield gen_obs

    client = MagicMock()
    client.start_as_current_observation.side_effect = [root_ctx(), img_ctx(), gen_ctx()]

    tracer = LangfuseInferenceTracer(client, redact_model_input=True, redact_model_output=False)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": "vision system"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "describe screenshot"},
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64,QUJDRA=="},
                },
            ],
        },
    ]

    with tracer.triage_issue_trace(run_id="r1", issue_key="TJC-9", project="TJC"):
        with tracer.image_context_extraction() as finish_img:
            with tracer.vision_generation(
                model="google/gemini-2.0-flash-001",
                messages=messages,
                model_parameters={"temperature": 0.0},
                attachment_id="att-1",
                filename="screen.png",
            ) as finish_vision:
                finish_vision(
                    "TRANSCRIPT:\nError\n\nSUMMARY:\nRed toast.",
                    {"attachment_id": "att-1", "filename": "screen.png"},
                    usage_details={"prompt_tokens": 100, "completion_tokens": 20},
                    cost_details={"cost": 0.002},
                )
            finish_img(
                attachments_considered=1,
                attachments_extracted=1,
                total_bytes=1024,
                total_vision_cost=0.002,
            )

    gen_call = client.start_as_current_observation.call_args_list[2]
    assert gen_call.kwargs["name"] == "inference_vision"
    assert gen_call.kwargs["as_type"] == "generation"
    assert gen_call.kwargs["model"] == "google/gemini-2.0-flash-001"
    traced = gen_call.kwargs["input"]
    assert traced[0]["content"] == "vision system"
    assert traced[1]["content"][0]["text"].startswith("[REDACTED] len=")
    assert "base64_len=8" in traced[1]["content"][1]["image_url"]["url"]
    assert gen_call.kwargs["metadata"]["attachment_id"] == "att-1"
    gen_obs.update.assert_called_once()
    ukwargs = gen_obs.update.call_args.kwargs
    assert "TRANSCRIPT:" in ukwargs["output"]
    assert ukwargs["usage_details"]["prompt_tokens"] == 100
    img_obs.update.assert_called_once()


@pytest.mark.unit
def test_build_langfuse_inference_tracer_passes_keys_to_client() -> None:
    from unittest.mock import patch

    with patch("triage_service.observability.langfuse_inference_tracing.Langfuse") as m:
        build_langfuse_inference_tracer(
            public_key="pk-lf-test",
            secret_key="sk-lf-test",
            base_url="https://example.invalid",
            redact_model_input=False,
            redact_model_output=False,
        )
    m.assert_called_once_with(
        public_key="pk-lf-test",
        secret_key="sk-lf-test",
        base_url="https://example.invalid",
    )
