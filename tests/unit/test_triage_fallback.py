"""Unit tests for the triage fallback/error response path.

Covers the structured ``TriageFailure`` payload returned when the pipeline cannot
produce a recommendation, plus the exception → failure mapper used by the
eventual orchestrator (Jira fetch, OpenRouter inference, model-output parsing).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from triage_service.adapters.jira_issue_fetcher import JiraIssueFetchError
from triage_service.adapters.openrouter_inference_client import OpenRouterInferenceError
from triage_service.core.triage_fallback import (
    ProjectNotAllowedError,
    TriageFailure,
    TriageFailureCategory,
    fallback_for_exception,
)
from triage_service.core.triage_recommendation_parser import InvalidTriageRecommendationError


@pytest.mark.unit
def test_triage_failure_accepts_known_category_and_non_empty_message() -> None:
    failure = TriageFailure(
        category="jira_fetch_failed",
        message="HTTP 500 from Jira",
    )
    assert failure.category == "jira_fetch_failed"
    assert failure.message == "HTTP 500 from Jira"


@pytest.mark.unit
def test_triage_failure_is_frozen_and_forbids_extra_fields() -> None:
    failure = TriageFailure(category="internal_error", message="boom")
    with pytest.raises(ValidationError):
        failure.message = "mutated"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        TriageFailure(
            category="internal_error",
            message="boom",
            extra="nope",  # type: ignore[call-arg]
        )


@pytest.mark.unit
def test_triage_failure_rejects_empty_message() -> None:
    with pytest.raises(ValidationError):
        TriageFailure(category="internal_error", message="")


@pytest.mark.unit
def test_triage_failure_rejects_whitespace_only_message() -> None:
    with pytest.raises(ValidationError):
        TriageFailure(category="internal_error", message="   \t\n")


@pytest.mark.unit
def test_triage_failure_strips_whitespace_in_message() -> None:
    failure = TriageFailure(category="internal_error", message="  bad json  ")
    assert failure.message == "bad json"


@pytest.mark.unit
def test_triage_failure_rejects_unknown_category() -> None:
    with pytest.raises(ValidationError):
        TriageFailure(category="not_a_real_category", message="boom")


@pytest.mark.unit
def test_failure_category_literal_lists_all_supported_values() -> None:
    """Category set is the contract surface other components consume; pin it."""
    expected = {
        "jira_fetch_failed",
        "inference_failed",
        "invalid_model_output",
        "internal_error",
        "project_not_allowed",
    }
    assert set(TriageFailureCategory.__args__) == expected  # type: ignore[attr-defined]


@pytest.mark.unit
def test_fallback_for_jira_fetch_error_maps_to_jira_fetch_failed() -> None:
    failure = fallback_for_exception(JiraIssueFetchError("HTTP 500: server error"))
    assert failure.category == "jira_fetch_failed"
    assert "HTTP 500" in failure.message


@pytest.mark.unit
def test_fallback_for_openrouter_error_maps_to_inference_failed() -> None:
    failure = fallback_for_exception(
        OpenRouterInferenceError("OpenRouter request failed with HTTP 429"),
    )
    assert failure.category == "inference_failed"
    assert "429" in failure.message


@pytest.mark.unit
def test_fallback_for_invalid_model_output_maps_to_invalid_model_output() -> None:
    failure = fallback_for_exception(
        InvalidTriageRecommendationError("Model output is not valid JSON."),
    )
    assert failure.category == "invalid_model_output"
    assert "JSON" in failure.message


@pytest.mark.unit
def test_fallback_for_project_not_allowed_maps_to_project_not_allowed() -> None:
    exc = ProjectNotAllowedError("Project XX is not allowed for triage.")
    failure = fallback_for_exception(exc)
    assert failure.category == "project_not_allowed"
    assert "XX" in failure.message


@pytest.mark.unit
def test_fallback_for_unknown_exception_maps_to_internal_error() -> None:
    failure = fallback_for_exception(RuntimeError("unexpected"))
    assert failure.category == "internal_error"
    assert "unexpected" in failure.message


@pytest.mark.unit
def test_fallback_uses_default_message_when_exception_message_is_blank() -> None:
    failure = fallback_for_exception(JiraIssueFetchError(""))
    assert failure.category == "jira_fetch_failed"
    assert failure.message.strip() != ""


@pytest.mark.unit
def test_fallback_result_is_json_serializable() -> None:
    failure = fallback_for_exception(
        OpenRouterInferenceError("OpenRouter response missing choices."),
    )
    dumped = failure.model_dump()
    assert dumped == {
        "category": "inference_failed",
        "message": "OpenRouter response missing choices.",
    }
