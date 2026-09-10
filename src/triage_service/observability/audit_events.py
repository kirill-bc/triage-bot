"""Canonical audit event schema for the triage lifecycle.

These events are the stable contract for structured logs, LangFuse spans, and
other audit sinks. Call sites (handler, adapters) construct or serialize them
at lifecycle boundaries:

- ``classification_completed`` — after inference step (1) parses successfully.
- ``priority_completed`` — after inference step (2) parses successfully (Bug path only).
- ``image_context_extracted`` — after vision preprocessing (when enabled) completes.
- ``zendesk_context_fetched`` — after linked Zendesk ticket fetch completes (when enabled).
- ``zendesk_context_summarized`` — after resolution-aware comment summarization (when enabled).
- ``triage_completed`` — final merged recommendation before/after executor success.
- ``triage_failed`` — pipeline returned :class:`~triage_service.core.triage_fallback.TriageFailure`.
- ``outcome_delivered`` — outcome hand-off finished (Jira Automation callback POST).

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


class ZendeskImageDedupeSkipDetail(BaseModel):
    """Zendesk image skipped because Jira already has a matching attachment."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ticket_id: str = Field(min_length=1)
    url: str = Field(min_length=1)
    filename: str | None = None
    skip_reason: str = Field(min_length=1)
    matched_jira_attachment_id: str | None = None


class ZendeskTicketFetchFailureDetail(BaseModel):
    """Per-ticket Zendesk fetch failure (soft-fail path)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ticket_id: str = Field(min_length=1)
    failure: str = Field(min_length=1)


class ZendeskContextFetchedAuditEvent(_CorrelationMixin):
    """Linked Zendesk ticket fetch finished (success and soft failures)."""

    event_type: Literal["zendesk_context_fetched"]
    ticket_ids_requested: list[str] = Field(default_factory=list)
    tickets_fetched: int = Field(ge=0, default=0)
    ticket_ids_deduped: int = Field(ge=0, default=0)
    fetch_failed: bool = False
    per_ticket_failures: list[ZendeskTicketFetchFailureDetail] = Field(default_factory=list)

    @model_validator(mode="after")
    def _fetched_not_greater_than_requested(self) -> ZendeskContextFetchedAuditEvent:
        if self.tickets_fetched > len(self.ticket_ids_requested):
            msg = "tickets_fetched cannot exceed ticket_ids_requested count"
            raise ValueError(msg)
        return self


class ZendeskTicketSummaryDetail(BaseModel):
    """Per-ticket Zendesk comment summarization outcome."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ticket_id: str = Field(min_length=1)
    summarized: bool
    inference_cost: float | None = Field(default=None, ge=0.0)
    failure: str | None = None


class ZendeskContextSummarizedAuditEvent(_CorrelationMixin):
    """Resolution-aware Zendesk summarization finished."""

    event_type: Literal["zendesk_context_summarized"]
    tickets_considered: int = Field(ge=0, default=0)
    tickets_summarized: int = Field(ge=0, default=0)
    total_summary_cost: float | None = Field(default=None, ge=0.0)
    per_ticket: list[ZendeskTicketSummaryDetail] = Field(default_factory=list)

    @model_validator(mode="after")
    def _summarized_not_greater_than_considered(self) -> ZendeskContextSummarizedAuditEvent:
        if self.tickets_summarized > self.tickets_considered:
            msg = "tickets_summarized cannot exceed tickets_considered"
            raise ValueError(msg)
        return self


class ImageContextExtractedAuditEvent(_CorrelationMixin):
    """Vision preprocessing finished (success and soft failures)."""

    event_type: Literal["image_context_extracted"]
    attachments_considered: int = Field(ge=0)
    attachments_extracted: int = Field(ge=0)
    total_bytes: int = Field(ge=0)
    total_vision_cost: float | None = Field(default=None, ge=0.0)
    per_attachment: list[ImageAttachmentExtractionDetail] = Field(default_factory=list)
    zendesk_skipped: list[ZendeskImageDedupeSkipDetail] = Field(default_factory=list)

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


class OutcomeDeliveredAuditEvent(_CorrelationMixin):
    """Outcome hand-off finished: Automation callback POST (or direct Jira writes).

    ``delivered`` records whether the hand-off itself succeeded, not whether Jira ultimately
    applied the changes — on the ``automation_webhook`` path the rule executes asynchronously,
    so its own audit log (correlated by ``run_id``) is the record of the applied result.
    """

    event_type: Literal["outcome_delivered"]
    delivery_mode: Literal["direct", "automation_webhook"]
    delivered: bool
    http_status: int | None = None
    attempts: int = Field(default=0, ge=0)
    failure: str | None = None

    @field_validator("failure", mode="before")
    @classmethod
    def _blank_failure_to_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def _failure_matches_delivered(self) -> OutcomeDeliveredAuditEvent:
        if self.delivered and self.failure is not None:
            msg = "failure must be null when delivered is true"
            raise ValueError(msg)
        if not self.delivered and self.failure is None:
            msg = "failure is required when delivered is false"
            raise ValueError(msg)
        return self


TriageAuditEvent = Annotated[
    Union[
        ClassificationCompletedAuditEvent,
        ImageContextExtractedAuditEvent,
        OutcomeDeliveredAuditEvent,
        PriorityCompletedAuditEvent,
        TriageCompletedAuditEvent,
        TriageFailedAuditEvent,
        ZendeskContextFetchedAuditEvent,
        ZendeskContextSummarizedAuditEvent,
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
