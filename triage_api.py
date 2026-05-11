"""HTTP API surface for triage triggers (MVP: request/response contract only).

The endpoint is fired by a Jira Automation **scheduled** rule (JQL-driven scan),
not by an event-driven hook. The request body therefore carries a ``source``
annotation rather than a Jira event type. ``scheduled_scan`` is the only MVP
value; future sources (e.g. ``manual_cli`` for the local runner) extend the
literal without changing the request shape.
"""

from __future__ import annotations

from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel, Field

TriageSource = Literal["scheduled_scan"]


class TriageRequest(BaseModel):
    """Inbound webhook-style payload for a single issue."""

    issue_key: str = Field(min_length=1, description="Jira issue key, e.g. TJC-123.")
    project: str = Field(min_length=1, description="Jira project key.")
    source: TriageSource = Field(
        description="Origin of the triage call (scheduled_scan for Jira Automation rules).",
    )


class TriageAccepted(BaseModel):
    """Acknowledgement that the trigger was accepted for processing."""

    issue_key: str
    project: str
    source: TriageSource
    status: str = Field(default="accepted", description="Processing state placeholder.")


def create_app() -> FastAPI:
    app = FastAPI(title="Jira Triage", version="0.1.0")

    @app.post("/triage", response_model=TriageAccepted)
    def accept_triage_trigger(body: TriageRequest) -> TriageAccepted:
        return TriageAccepted(
            issue_key=body.issue_key,
            project=body.project,
            source=body.source,
        )

    return app


app = create_app()
