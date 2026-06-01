"""Resolution-aware Zendesk comment summarization for triage context."""

from __future__ import annotations

import re
from typing import Protocol

from pydantic import BaseModel, Field

from triage_service.adapters.jira_issue_fetcher import (
    FetchedIssue,
    LinkedZendeskTicket,
    ZendeskCommentRef,
    ZendeskResolutionSummary,
    zendesk_comment_newest_first_sort_key,
)
from triage_service.adapters.openrouter_inference_client import (
    OpenRouterInferenceClient,
    OpenRouterInferenceError,
)
from triage_service.core.settings import AppSettings
from triage_service.core.zendesk_summary_prompt_composer import (
    compose_zendesk_summary_system_prompt,
    compose_zendesk_summary_user_instruction,
)
from triage_service.observability.langfuse_inference_tracing import LangfuseInferenceTracer

_SECTION_NAMES = (
    "INITIAL_IMPACT",
    "LATEST_STATUS",
    "RESOLUTION_HINTS",
    "OPEN_RISKS",
)
_SECTION_PATTERN = re.compile(
    r"^(?P<name>INITIAL_IMPACT|LATEST_STATUS|RESOLUTION_HINTS|OPEN_RISKS):\s*$",
    re.MULTILINE,
)


class ZendeskTicketSummaryMetric(BaseModel):
    """Per-ticket summarization telemetry."""

    ticket_id: str = Field(min_length=1)
    summarized: bool
    inference_cost: float | None = Field(default=None, ge=0.0)
    failure: str | None = None


class ZendeskSummarizationResult(BaseModel):
    """Tickets with optional resolution summaries plus aggregate telemetry."""

    tickets: list[LinkedZendeskTicket] = Field(default_factory=list)
    tickets_considered: int = Field(ge=0, default=0)
    tickets_summarized: int = Field(ge=0, default=0)
    total_inference_cost: float | None = Field(default=None, ge=0.0)
    per_ticket: list[ZendeskTicketSummaryMetric] = Field(default_factory=list)


class ZendeskCommentSummarizer(Protocol):
    """Condenses Zendesk ticket threads into resolution-aware signals."""

    def summarize(
        self,
        issue: FetchedIssue,
        tickets: list[LinkedZendeskTicket],
        *,
        run_id: str,
    ) -> ZendeskSummarizationResult:
        """Return tickets with resolution_summary populated where possible."""


class NoOpZendeskCommentSummarizer:
    """Feature-off path: no summarization calls."""

    def summarize(
        self,
        issue: FetchedIssue,
        tickets: list[LinkedZendeskTicket],
        *,
        run_id: str,
    ) -> ZendeskSummarizationResult:
        _ = (issue, run_id)
        return ZendeskSummarizationResult(tickets=list(tickets))


def build_zendesk_comment_summarizer(
    settings: AppSettings,
    *,
    inference_client: OpenRouterInferenceClient | None = None,
    inference_tracer: LangfuseInferenceTracer | None = None,
) -> ZendeskCommentSummarizer:
    """Return NoOp when disabled; otherwise an OpenRouter-backed summarizer."""
    if not settings.triage_zendesk_comment_summary_enabled:
        return NoOpZendeskCommentSummarizer()
    model_override = (settings.triage_zendesk_summary_model or "").strip() or None
    inference = inference_client or OpenRouterInferenceClient(
        settings,
        model_override=model_override,
        http_timeout_seconds=settings.triage_zendesk_summary_timeout_seconds,
    )
    return OpenRouterZendeskCommentSummarizer(
        settings=settings,
        inference_client=inference,
        comments_char_budget=settings.triage_zendesk_comments_char_budget,
        inference_tracer=inference_tracer,
    )


def trim_zendesk_comments_for_budget(
    comments: list[ZendeskCommentRef],
    *,
    char_budget: int,
) -> list[ZendeskCommentRef]:
    """Keep a newest-first contiguous prefix within the character budget."""
    if not comments or char_budget <= 0:
        return []
    ordered = sorted(
        comments,
        key=zendesk_comment_newest_first_sort_key,
        reverse=True,
    )
    selected: list[ZendeskCommentRef] = []
    used = 0
    for comment in ordered:
        body_len = len(comment.body)
        if body_len == 0:
            selected.append(comment)
            continue
        if used + body_len > char_budget:
            break
        selected.append(comment)
        used += body_len
    return selected


def parse_zendesk_resolution_summary(content: str) -> ZendeskResolutionSummary | None:
    """Parse fixed Zendesk summary sections from model output."""
    matches = list(_SECTION_PATTERN.finditer(content))
    if len(matches) < len(_SECTION_NAMES):
        return None
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        name = match.group("name")
        if name in sections:
            continue
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        value = content[start:end].strip()
        if not value:
            return None
        sections[name] = value
    try:
        return ZendeskResolutionSummary(
            initial_impact=sections["INITIAL_IMPACT"],
            latest_status=sections["LATEST_STATUS"],
            resolution_hints=sections["RESOLUTION_HINTS"],
            open_risks=sections["OPEN_RISKS"],
        )
    except KeyError:
        return None


def _inference_cost_from_details(cost_details: dict[str, float] | None) -> float | None:
    if not cost_details:
        return None
    total = cost_details.get("total") or cost_details.get("total_cost")
    if total is None:
        return None
    return float(total)


class OpenRouterZendeskCommentSummarizer:
    """OpenRouter-backed per-ticket Zendesk comment summarizer."""

    def __init__(
        self,
        *,
        settings: AppSettings,
        inference_client: OpenRouterInferenceClient,
        comments_char_budget: int,
        inference_tracer: LangfuseInferenceTracer | None = None,
    ) -> None:
        self._settings = settings
        self._inference = inference_client
        self._comments_char_budget = comments_char_budget
        self._inference_tracer = inference_tracer or LangfuseInferenceTracer(None)

    def summarize(
        self,
        issue: FetchedIssue,
        tickets: list[LinkedZendeskTicket],
        *,
        run_id: str,
    ) -> ZendeskSummarizationResult:
        if not tickets:
            return ZendeskSummarizationResult()
        summarized_tickets: list[LinkedZendeskTicket] = []
        metrics: list[ZendeskTicketSummaryMetric] = []
        summarized_count = 0
        costs: list[float] = []
        for ticket in tickets:
            updated, metric = self._summarize_one(issue, ticket, run_id=run_id)
            summarized_tickets.append(updated)
            metrics.append(metric)
            if metric.summarized:
                summarized_count += 1
            if metric.inference_cost is not None:
                costs.append(metric.inference_cost)
        total_cost = sum(costs) if costs else None
        return ZendeskSummarizationResult(
            tickets=summarized_tickets,
            tickets_considered=len(tickets),
            tickets_summarized=summarized_count,
            total_inference_cost=total_cost,
            per_ticket=metrics,
        )

    def _summarize_one(
        self,
        issue: FetchedIssue,
        ticket: LinkedZendeskTicket,
        *,
        run_id: str,
    ) -> tuple[LinkedZendeskTicket, ZendeskTicketSummaryMetric]:
        trimmed_comments = trim_zendesk_comments_for_budget(
            ticket.comments,
            char_budget=self._comments_char_budget,
        )
        messages = [
            {
                "role": "system",
                "content": compose_zendesk_summary_system_prompt(settings=self._settings),
            },
            {
                "role": "user",
                "content": compose_zendesk_summary_user_instruction(
                    issue,
                    ticket,
                    trimmed_comments,
                    settings=self._settings,
                ),
            },
        ]
        model_id = self._inference.effective_model_id
        with self._inference_tracer.zendesk_summary_generation(
            model=model_id,
            messages=messages,
            model_parameters={"temperature": 0.0},
            ticket_id=ticket.ticket_id,
        ) as finish_gen:
            try:
                result = self._inference.chat_completion_with_details(
                    messages,
                    run_id=run_id,
                    temperature=0.0,
                )
            except OpenRouterInferenceError:
                finish_gen("", {"failure": "inference_failed"})
                metric = ZendeskTicketSummaryMetric(
                    ticket_id=ticket.ticket_id,
                    summarized=False,
                    failure="inference_failed",
                )
                return ticket, metric
            summary = parse_zendesk_resolution_summary(result.content)
            cost = _inference_cost_from_details(result.cost_details)
            finish_gen(
                result.content,
                {"parsed": summary.model_dump(mode="json") if summary else None},
                usage_details=result.usage_details,
                cost_details=result.cost_details,
            )
        if summary is None:
            metric = ZendeskTicketSummaryMetric(
                ticket_id=ticket.ticket_id,
                summarized=False,
                inference_cost=cost,
                failure="parse_failed",
            )
            return ticket, metric
        metric = ZendeskTicketSummaryMetric(
            ticket_id=ticket.ticket_id,
            summarized=True,
            inference_cost=cost,
        )
        return ticket.model_copy(update={"resolution_summary": summary}), metric
