"""Fallback/error response shape for the triage pipeline.

When the pipeline cannot produce a valid ``TriageRecommendation`` because an
upstream call failed or model output did not satisfy the strict schema, the
orchestrator returns a structured :class:`TriageFailure` instead.

The supported categories cover every error surface the pipeline owns:

- ``jira_fetch_failed`` — raised by :class:`jira_issue_fetcher.JiraIssueFetcher`
  when configuration is missing or the Jira REST call returns a non-2xx response.
- ``inference_failed`` — raised by
  :class:`openrouter_inference_client.OpenRouterInferenceClient` on HTTP errors
  or unusable completion payloads.
- ``invalid_model_output`` — raised by the recommendation parser when the model
  reply is not JSON or fails the schema (see
  :class:`triage_recommendation_parser.InvalidTriageRecommendationError`).
- ``internal_error`` — catch-all for unexpected exceptions; keeps the response
  contract closed so callers never see a raw traceback.
- ``project_not_allowed`` — raised when the inbound ``project`` key is not on the
  server allowlist (misconfigured Jira rule safety net).

``fallback_for_exception`` is the single conversion point used by the
orchestrator: it inspects the exception type and produces a typed failure with
a non-empty human-readable message. Phase 1 action executors should treat any
``TriageFailure`` as "do not post a Jira comment or label" — failures are
**advisory** signal for logs/metrics, not user-facing recommendations.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

from jira_issue_fetcher import JiraIssueFetchError
from openrouter_inference_client import OpenRouterInferenceError
from triage_recommendation_parser import InvalidTriageRecommendationError


class ProjectNotAllowedError(ValueError):
    """Raised when ``project`` is not on the server allowlist (``TriageCoreConfig``)."""


TriageFailureCategory = Literal[
    "jira_fetch_failed",
    "inference_failed",
    "invalid_model_output",
    "internal_error",
    "project_not_allowed",
]

_DEFAULT_MESSAGES: dict[str, str] = {
    "jira_fetch_failed": "Jira issue fetch failed.",
    "inference_failed": "Model inference failed.",
    "invalid_model_output": "Model output failed schema validation.",
    "internal_error": "Unexpected internal error.",
    "project_not_allowed": "Project is not allowed for triage.",
}


class TriageFailure(BaseModel):
    """Structured fallback returned when triage cannot produce a recommendation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    category: TriageFailureCategory
    message: str

    @field_validator("message", mode="before")
    @classmethod
    def _strip_message(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("message")
    @classmethod
    def _message_non_empty(cls, value: str) -> str:
        if not value:
            msg = "message must be a non-empty string"
            raise ValueError(msg)
        return value


def _message_for(exc: BaseException, category: TriageFailureCategory) -> str:
    raw = str(exc).strip()
    return raw if raw else _DEFAULT_MESSAGES[category]


def fallback_for_exception(exc: BaseException) -> TriageFailure:
    """Map a known pipeline exception (or any other) to a typed failure."""
    if isinstance(exc, JiraIssueFetchError):
        category: TriageFailureCategory = "jira_fetch_failed"
    elif isinstance(exc, OpenRouterInferenceError):
        category = "inference_failed"
    elif isinstance(exc, InvalidTriageRecommendationError):
        category = "invalid_model_output"
    elif isinstance(exc, ProjectNotAllowedError):
        category = "project_not_allowed"
    else:
        category = "internal_error"
    return TriageFailure(category=category, message=_message_for(exc, category))
