"""HTTP API surface for triage triggers (MVP: request/response contract only).

The endpoint is fired by a Jira Automation **scheduled** rule (JQL-driven scan),
not by an event-driven hook. The request body therefore carries a ``source``
annotation rather than a Jira event type. ``scheduled_scan`` is the only MVP
value; ``manual_cli`` is used by the local CLI runner; future sources extend the
literal without changing the request shape.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from fastapi import Depends, FastAPI
from pydantic import BaseModel, Field, model_validator
from typing_extensions import Self

from triage_fallback import TriageFailure
from triage_handler import TriageRunner, build_default_triage_handler
from triage_recommendation_parser import TriageRecommendation

TriageSource = Literal["scheduled_scan", "manual_cli"]


class TriageRequest(BaseModel):
    """Inbound webhook-style payload for a single issue."""

    issue_key: str = Field(min_length=1, description="Jira issue key, e.g. TJC-123.")
    project: str = Field(min_length=1, description="Jira project key.")
    source: TriageSource = Field(
        description=(
            "Origin of the triage call: scheduled_scan (Jira Automation) or "
            "manual_cli (local runner)."
        ),
    )


class TriagePostResponse(BaseModel):
    """Synchronous triage outcome: merged recommendation or structured failure."""

    issue_key: str
    project: str
    source: TriageSource
    status: Literal["completed", "failed"]
    recommendation: TriageRecommendation | None = None
    failure: TriageFailure | None = None

    @model_validator(mode="after")
    def _outcome_matches_status(self) -> Self:
        if self.status == "completed":
            if self.recommendation is None:
                msg = "completed status requires recommendation"
                raise ValueError(msg)
            if self.failure is not None:
                msg = "completed status must not include failure"
                raise ValueError(msg)
        else:
            if self.failure is None:
                msg = "failed status requires failure"
                raise ValueError(msg)
            if self.recommendation is not None:
                msg = "failed status must not include recommendation"
                raise ValueError(msg)
        return self


def create_app(*, triage_handler_factory: Callable[[], TriageRunner] | None = None) -> FastAPI:
    """Build the FastAPI app. Override ``triage_handler_factory`` in tests."""
    factory: Callable[[], TriageRunner] = triage_handler_factory or build_default_triage_handler

    def get_triage_runner() -> TriageRunner:
        return factory()

    app = FastAPI(title="Jira Triage", version="0.1.0")

    @app.post("/triage", response_model=TriagePostResponse)
    def accept_triage_trigger(
        body: TriageRequest,
        runner: TriageRunner = Depends(get_triage_runner),
    ) -> TriagePostResponse:
        outcome = runner.run_sync(body.issue_key, body.project, body.source)
        if isinstance(outcome, TriageFailure):
            return TriagePostResponse(
                issue_key=body.issue_key,
                project=body.project,
                source=body.source,
                status="failed",
                failure=outcome,
            )
        return TriagePostResponse(
            issue_key=body.issue_key,
            project=body.project,
            source=body.source,
            status="completed",
            recommendation=outcome,
        )

    return app


app = create_app()
