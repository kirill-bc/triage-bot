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
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

_BUG_PRIORITIES = frozenset({"P0", "P1", "P2", "P3", "P4"})

IssueTypeLiteral = Literal["Bug", "Story"]

_RAW_OUTPUT_MAX_CHARS = 2000

_CODE_FENCE_PATTERN = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL | re.IGNORECASE)


class InvalidTriageRecommendationError(ValueError):
    """Raised when model output is not valid JSON or does not match the triage schema."""

    def __init__(self, message: str, *, raw_output: str | None = None) -> None:
        super().__init__(message)
        self.raw_output = raw_output


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


def parse_triage_recommendation_json(
    data: dict[str, Any],
    *,
    raw_output: str | None = None,
) -> TriageRecommendation:
    """Validate a decoded JSON object against the merged triage schema."""
    try:
        return TriageRecommendation.model_validate(_without_legacy_llm_keys(data))
    except ValidationError as exc:
        detail = exc.errors(include_url=False, include_context=False)
        raise InvalidTriageRecommendationError(
            f"Invalid triage recommendation: {detail}",
            raw_output=raw_output,
        ) from exc


def parse_triage_recommendation_text(text: str) -> TriageRecommendation:
    """Parse JSON text (optionally markdown-fenced or wrapped in prose) into a recommendation."""
    data = _parse_json_object_text(text, label="Model output")
    return parse_triage_recommendation_json(data, raw_output=_truncate_raw_output(text))


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


def _truncate_raw_output(text: str) -> str:
    """Bound the raw model text kept on the exception for audit/log payloads."""
    stripped = text.strip()
    if len(stripped) <= _RAW_OUTPUT_MAX_CHARS:
        return stripped
    return stripped[:_RAW_OUTPUT_MAX_CHARS] + "... [truncated]"


def _strip_code_fence(text: str) -> str:
    """Strip a single wrapping markdown code fence (```` ``` ```` or ```` ```json ````).

    Reasoning-heavy models sometimes present a valid JSON object wrapped in a fence despite
    system-prompt instructions not to; this is presentation only, not a schema problem.
    """
    stripped = text.strip()
    match = _CODE_FENCE_PATTERN.match(stripped)
    return match.group(1).strip() if match else stripped


def _extract_first_json_object(text: str) -> object:
    """Best-effort extraction of the first JSON object embedded in surrounding prose."""
    start = text.find("{")
    if start == -1:
        raise json.JSONDecodeError("No JSON object found in model output.", text, 0)
    return json.JSONDecoder().raw_decode(text, start)[0]


def _parse_json_object_text(text: str, *, label: str) -> dict[str, Any]:
    unfenced = _strip_code_fence(text)
    try:
        decoded: object = json.loads(unfenced)
    except json.JSONDecodeError:
        try:
            decoded = _extract_first_json_object(unfenced)
        except json.JSONDecodeError as exc:
            raise InvalidTriageRecommendationError(
                f"{label} is not valid JSON.",
                raw_output=_truncate_raw_output(text),
            ) from exc
    if not isinstance(decoded, dict):
        msg = f"{label} JSON must be an object at the top level."
        raise InvalidTriageRecommendationError(msg, raw_output=_truncate_raw_output(text))
    return decoded


def parse_classification_step_text(text: str) -> ClassificationStepOutput:
    """Parse step (1) JSON: Story or Bug without ``recommended_priority``."""
    raw = _parse_json_object_text(text, label="Classification model output")
    data = _without_legacy_llm_keys(raw)
    try:
        return ClassificationStepOutput.model_validate(data)
    except ValidationError as exc:
        detail = exc.errors(include_url=False, include_context=False)
        raise InvalidTriageRecommendationError(
            f"Invalid classification step: {detail}",
            raw_output=_truncate_raw_output(text),
        ) from exc


def parse_priority_step_text(text: str) -> PriorityStepOutput:
    """Parse step (2) JSON for Bug path: P0–P4 plus confidence and reason."""
    raw = _parse_json_object_text(text, label="Priority model output")
    data = _without_legacy_llm_keys(raw)
    try:
        return PriorityStepOutput.model_validate(data)
    except ValidationError as exc:
        detail = exc.errors(include_url=False, include_context=False)
        raise InvalidTriageRecommendationError(
            f"Invalid priority step: {detail}",
            raw_output=_truncate_raw_output(text),
        ) from exc


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
