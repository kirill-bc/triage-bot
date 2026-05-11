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
- ``recommended_action``: one of ``comment_only``, ``label``, ``reclassify``,
  ``update_priority`` (see ``specification.md``).
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

_BUG_PRIORITIES = frozenset({"P0", "P1", "P2", "P3", "P4"})

RecommendedActionLiteral = Literal["comment_only", "label", "reclassify", "update_priority"]
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
    recommended_action: RecommendedActionLiteral

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
        return TriageRecommendation.model_validate(data)
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
