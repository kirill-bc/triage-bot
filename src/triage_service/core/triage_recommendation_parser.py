"""Parse and validate triage model JSON into a strict recommendation schema.

Merged payloads (single JSON object) must include:

- ``recommended_issue_type``: ``Bug`` or ``Story``.
- ``recommended_priority``: required ``P0``–``P4`` when type is ``Bug``; must be
  omitted or ``null`` when type is ``Story`` (priority inference is not used on
  the Story path).
- ``confidence``: float in ``[0.0, 1.0]``. For merged responses produced by the
  service from two steps, this field should reflect the **last inference that ran**
  (classification only for Story; priority step when type is Bug).
- ``reason``: non-empty string after stripping leading/trailing whitespace.

``recommended_action`` is not part of the contract; callers derive labels/comments from
:class:`triage_mismatch.TriageMismatchFlags` plus ``reason`` (and optional ``confidence`` for
audit). Legacy model JSON that still includes ``recommended_action`` is ignored at parse time.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

_BUG_PRIORITIES = frozenset({"P0", "P1", "P2", "P3", "P4"})

IssueTypeLiteral = Literal["Bug", "Story"]


class InvalidTriageRecommendationError(ValueError):
    """Raised when model output is not valid JSON or does not match the triage schema."""


class TriageRecommendation(BaseModel):
    """Validated merged triage recommendation from model output."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    recommended_issue_type: IssueTypeLiteral
    recommended_priority: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str

    @field_validator("reason", mode="before")
    @classmethod
    def _strip_reason(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("reason")
    @classmethod
    def _reason_non_empty(cls, value: str) -> str:
        if not value:
            msg = "reason must be a non-empty string"
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def _priority_matches_issue_type(self) -> TriageRecommendation:
        if self.recommended_issue_type == "Story":
            if self.recommended_priority is not None:
                msg = (
                    "recommended_priority must be null or omitted "
                    "when recommended_issue_type is Story"
                )
                raise ValueError(msg)
            return self
        if self.recommended_priority not in _BUG_PRIORITIES:
            msg = (
                "recommended_priority must be one of P0, P1, P2, P3, P4 "
                "when recommended_issue_type is Bug"
            )
            raise ValueError(msg)
        return self


def parse_triage_recommendation_json(data: dict[str, Any]) -> TriageRecommendation:
    """Validate a decoded JSON object against the merged triage schema."""
    try:
        return TriageRecommendation.model_validate(_without_legacy_llm_keys(data))
    except ValidationError as exc:
        detail = exc.errors(include_url=False, include_context=False)
        raise InvalidTriageRecommendationError(f"Invalid triage recommendation: {detail}") from exc


def parse_triage_recommendation_text(text: str) -> TriageRecommendation:
    """Parse non-empty JSON text (object) and return a validated ``TriageRecommendation``."""
    stripped = text.strip()
    try:
        decoded: object = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise InvalidTriageRecommendationError("Model output is not valid JSON.") from exc
    if not isinstance(decoded, dict):
        msg = "Model output JSON must be an object at the top level."
        raise InvalidTriageRecommendationError(msg)
    return parse_triage_recommendation_json(decoded)


class ClassificationStepOutput(BaseModel):
    """Validated output for inference step (1) before optional priority step (2)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    recommended_issue_type: IssueTypeLiteral
    recommended_priority: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str

    @field_validator("reason", mode="before")
    @classmethod
    def _strip_reason(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("reason")
    @classmethod
    def _reason_non_empty(cls, value: str) -> str:
        if not value:
            msg = "reason must be a non-empty string"
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def _priority_rules(self) -> ClassificationStepOutput:
        if self.recommended_issue_type == "Story":
            if self.recommended_priority is not None:
                msg = "classification step must omit recommended_priority when type is Story"
                raise ValueError(msg)
            return self
        if self.recommended_priority is not None:
            msg = (
                "classification step must not set recommended_priority for Bug; "
                "priority is produced in a separate inference step"
            )
            raise ValueError(msg)
        return self


class PriorityStepOutput(BaseModel):
    """Validated output for Bug-path priority inference step (2)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    recommended_priority: Literal["P0", "P1", "P2", "P3", "P4"]
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str

    @field_validator("reason", mode="before")
    @classmethod
    def _strip_reason(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("reason")
    @classmethod
    def _reason_non_empty(cls, value: str) -> str:
        if not value:
            msg = "reason must be a non-empty string"
            raise ValueError(msg)
        return value


def _without_legacy_llm_keys(data: dict[str, Any]) -> dict[str, Any]:
    """Drop keys the service no longer models (LLMs may still emit them)."""
    return {k: v for k, v in data.items() if k != "recommended_action"}


def _parse_json_object_text(text: str, *, label: str) -> dict[str, Any]:
    stripped = text.strip()
    try:
        decoded: object = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise InvalidTriageRecommendationError(f"{label} is not valid JSON.") from exc
    if not isinstance(decoded, dict):
        msg = f"{label} JSON must be an object at the top level."
        raise InvalidTriageRecommendationError(msg)
    return decoded


def parse_classification_step_text(text: str) -> ClassificationStepOutput:
    """Parse step (1) JSON: Story or Bug without ``recommended_priority``."""
    raw = _parse_json_object_text(text, label="Classification model output")
    data = _without_legacy_llm_keys(raw)
    try:
        return ClassificationStepOutput.model_validate(data)
    except ValidationError as exc:
        detail = exc.errors(include_url=False, include_context=False)
        raise InvalidTriageRecommendationError(f"Invalid classification step: {detail}") from exc


def parse_priority_step_text(text: str) -> PriorityStepOutput:
    """Parse step (2) JSON for Bug path: P0–P4 plus confidence and reason."""
    raw = _parse_json_object_text(text, label="Priority model output")
    data = _without_legacy_llm_keys(raw)
    try:
        return PriorityStepOutput.model_validate(data)
    except ValidationError as exc:
        detail = exc.errors(include_url=False, include_context=False)
        raise InvalidTriageRecommendationError(f"Invalid priority step: {detail}") from exc


def classification_story_to_final(step: ClassificationStepOutput) -> TriageRecommendation:
    """Build merged :class:`TriageRecommendation` when step (1) concludes Story."""
    if step.recommended_issue_type != "Story":
        msg = "classification_story_to_final requires Story classification"
        raise ValueError(msg)
    return TriageRecommendation(
        recommended_issue_type="Story",
        recommended_priority=None,
        confidence=step.confidence,
        reason=step.reason,
    )


def merge_bug_classification_with_priority(
    classification: ClassificationStepOutput,
    priority: PriorityStepOutput,
) -> TriageRecommendation:
    """Merge step (1) Bug with step (2); confidence and rationale follow the last inference."""
    if classification.recommended_issue_type != "Bug":
        msg = "merge_bug_classification_with_priority requires Bug classification"
        raise ValueError(msg)
    return TriageRecommendation(
        recommended_issue_type="Bug",
        recommended_priority=priority.recommended_priority,
        confidence=priority.confidence,
        reason=priority.reason,
    )
