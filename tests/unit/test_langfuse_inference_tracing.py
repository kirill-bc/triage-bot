"""Unit tests for LangFuse-backed inference step tracing."""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock, call

import pytest

from triage_service.observability.langfuse_inference_tracing import (
    LangfuseInferenceTracer,
    build_langfuse_inference_tracer,
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
def test_build_langfuse_inference_tracer_noop_without_keys() -> None:
    tracer = build_langfuse_inference_tracer(public_key=None, secret_key=None)
    assert tracer._client is None
    tracer_partial = build_langfuse_inference_tracer(public_key="pk", secret_key="  ")
    assert tracer_partial._client is None


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
