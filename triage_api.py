"""HTTP API surface for triage triggers (MVP: request/response contract only)."""

from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel, Field


class TriageRequest(BaseModel):
    """Inbound webhook-style payload for a single issue."""

    issue_key: str = Field(min_length=1, description="Jira issue key, e.g. TJC-123.")
    project: str = Field(min_length=1, description="Jira project key.")
    event_type: str = Field(min_length=1, description="Automation or webhook event name.")


class TriageAccepted(BaseModel):
    """Acknowledgement that the trigger was accepted for processing."""

    issue_key: str
    project: str
    event_type: str
    status: str = Field(default="accepted", description="Processing state placeholder.")


def create_app() -> FastAPI:
    app = FastAPI(title="Jira Triage", version="0.1.0")

    @app.post("/triage", response_model=TriageAccepted)
    def accept_triage_trigger(body: TriageRequest) -> TriageAccepted:
        return TriageAccepted(
            issue_key=body.issue_key,
            project=body.project,
            event_type=body.event_type,
        )

    return app


app = create_app()
