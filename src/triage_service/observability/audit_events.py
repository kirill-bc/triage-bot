"""Canonical audit event schema for the triage lifecycle.

These events are the stable contract for structured logs, LangFuse spans, and
other audit sinks. Call sites (handler, adapters) construct or serialize them
at lifecycle boundaries:

- ``classification_completed`` — after inference step (1) parses successfully.
- ``priority_completed`` — after inference step (2) parses successfully (Bug path only).
- ``image_context_extracted`` — after vision preprocessing (when enabled) completes.
- ``triage_completed`` — final merged recommendation before/after executor success.
- ``triage_failed`` — pipeline returned :class:`~triage_service.core.triage_fallback.TriageFailure`.

``TriageAuditFailureCategory`` values are kept aligned with
``TriageFailureCategory`` (see unit test); observability stays independent of
``core`` imports.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

TriageAuditFailureCategory = Literal[
    "jira_fetch_failed",
    "inference_failed",
    "invalid_model_output",
    "internal_error",
    "project_not_allowed",
]

TriageSourceLiteral = Literal["bug_created", "priority_changed", "manual_trigger"]

IssueTypeLiteral = Literal["Bug", "Story"]

_BUG_PRIORITIES = frozenset({"P0", "P1", "P2", "P3", "P4"})


class _CorrelationMixin(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str = Field(min_length=1)
    issue_key: str = Field(min_length=1)
    project: str = Field(min_length=1)
    source: TriageSourceLiteral
    telemetry: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Optional resilience metadata: http_attempts, http_status, transport_timeout, "
            "transport_error_kind, failure_category (query-friendly), boundary."
        ),
    )


class ClassificationCompletedAuditEvent(_CorrelationMixin):
    """Model output from classification step (1) is valid."""

    event_type: Literal["classification_completed"]
    recommended_issue_type: IssueTypeLiteral
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


class ImageAttachmentExtractionDetail(BaseModel):
    """Per-attachment vision preprocessing metrics."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    attachment_id: str = Field(min_length=1)
    filename: str = Field(min_length=1)
    latency_ms: float = Field(ge=0.0)
    bytes_fetched: int = Field(ge=0, default=0)
    extraction_failure: str | None = None


class ImageContextExtractedAuditEvent(_CorrelationMixin):
    """Vision preprocessing finished (success and soft failures)."""

    event_type: Literal["image_context_extracted"]
    attachments_considered: int = Field(ge=0)
    attachments_extracted: int = Field(ge=0)
    total_bytes: int = Field(ge=0)
    total_vision_cost: float | None = Field(default=None, ge=0.0)
    per_attachment: list[ImageAttachmentExtractionDetail] = Field(default_factory=list)

    @model_validator(mode="after")
    def _extracted_not_greater_than_considered(self) -> ImageContextExtractedAuditEvent:
        if self.attachments_extracted > self.attachments_considered:
            msg = "attachments_extracted cannot exceed attachments_considered"
            raise ValueError(msg)
        return self


class PriorityCompletedAuditEvent(_CorrelationMixin):
    """Model output from priority step (2) is valid (Bug path)."""

    event_type: Literal["priority_completed"]
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


class TriageCompletedAuditEvent(_CorrelationMixin):
    """Final triage outcome (merged recommendation) before returning to caller."""

    event_type: Literal["triage_completed"]
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
    def _priority_matches_issue_type(self) -> TriageCompletedAuditEvent:
        if self.recommended_issue_type == "Story":
            if self.recommended_priority is not None:
                msg = (
                    "recommended_priority must be null when "
                    "recommended_issue_type is Story"
                )
                raise ValueError(msg)
            return self
        if self.recommended_priority not in _BUG_PRIORITIES:
            msg = (
                "recommended_priority must be one of P0–P4 when "
                "recommended_issue_type is Bug"
            )
            raise ValueError(msg)
        return self


class TriageFailedAuditEvent(_CorrelationMixin):
    """Pipeline stopped with a structured failure (no Jira success side effects)."""

    event_type: Literal["triage_failed"]
    category: TriageAuditFailureCategory
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


TriageAuditEvent = Annotated[
    Union[
        ClassificationCompletedAuditEvent,
        ImageContextExtractedAuditEvent,
        PriorityCompletedAuditEvent,
        TriageCompletedAuditEvent,
        TriageFailedAuditEvent,
    ],
    Field(discriminator="event_type"),
]

_triage_audit_event_adapter: TypeAdapter[TriageAuditEvent] = TypeAdapter(TriageAuditEvent)


def parse_triage_audit_event(data: dict[str, Any]) -> TriageAuditEvent:
    """Validate a decoded JSON object as a :class:`TriageAuditEvent`."""
    return _triage_audit_event_adapter.validate_python(data)


def dump_triage_audit_event(event: TriageAuditEvent) -> dict[str, Any]:
    """Serialize an audit event for JSON logging or message queues."""
    return event.model_dump(mode="json", exclude_none=True)
