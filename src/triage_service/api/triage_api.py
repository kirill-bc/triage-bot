"""HTTP API surface for triage triggers (MVP: request/response contract only).

The request body carries a ``source`` closed enum so callers identify why triage
was invoked: ``bug_created`` (Jira automation on new bugs), ``priority_changed``
(Jira automation on priority edits), or ``manual_trigger`` (local runner / scripts).
"""

from __future__ import annotations

import os
import sys
import uuid
from hmac import compare_digest
from collections.abc import Awaitable, Callable
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel, Field, model_validator
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from typing_extensions import Self

from triage_service.core.settings import load_settings
from triage_service.core.triage_fallback import TriageFailure
from triage_service.core.triage_handler import TriageRunner, build_default_triage_handler
from triage_service.core.triage_recommendation_parser import TriageRecommendation
from triage_service.observability.log_payload_guard import preview_bytes_for_log
from triage_service.observability.observability_wiring import observability_status_summary

TriageSource = Literal["bug_created", "priority_changed", "manual_trigger"]


def triage_inbound_debug_enabled() -> bool:
    """True when ``TRIAGE_DEBUG_INBOUND`` requests raw ``POST /triage`` body logging to stderr."""
    token = os.environ.get("TRIAGE_DEBUG_INBOUND", "").strip().lower()
    return token in ("1", "true", "yes", "on")


def preview_request_body_for_log(body: bytes, *, max_len: int = 8192) -> str:
    """Return a UTF-8 string or repr for logging; truncate very large bodies."""
    return preview_bytes_for_log(body, max_bytes=max_len)


class _DebugInboundTriageBodyMiddleware(BaseHTTPMiddleware):
    """Log raw request bodies before validation (development / Jira payload debugging)."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if request.method != "POST" or request.url.path != "/triage":
            return await call_next(request)
        if not triage_inbound_debug_enabled():
            return await call_next(request)
        body = await request.body()
        content_type = request.headers.get("content-type", "")
        preview = preview_request_body_for_log(body)
        print(
            "[TRIAGE_DEBUG_INBOUND] POST /triage\n"
            f"  content-type: {content_type}\n"
            f"  body ({len(body)} bytes): {preview}",
            file=sys.stderr,
            flush=True,
        )

        async def receive() -> dict[str, str | bytes | bool]:
            return {"type": "http.request", "body": body, "more_body": False}

        replayed = Request(request.scope, receive)
        return await call_next(replayed)


class TriageRequest(BaseModel):
    """Inbound webhook-style payload for a single issue."""

    issue_key: str = Field(min_length=1, description="Jira issue key, e.g. TJC-123.")
    project: str = Field(min_length=1, description="Jira project key.")
    source: TriageSource = Field(
        description=(
            "Origin of the triage call: bug_created or priority_changed (Jira Automation) "
            "or manual_trigger (local runner)."
        ),
    )


class ObservabilityHealth(BaseModel):
    """Safe Langfuse / audit flags for operators (no secret values)."""

    langfuse_public_key_present: bool
    langfuse_secret_key_present: bool
    langfuse_base_url_configured: bool
    langfuse_inference_enabled: bool
    langfuse_sdk_tracing_env_enabled: bool
    otel_sdk_disabled: bool
    langfuse_export_env_ready: bool
    langfuse_prompt_management_enabled: bool
    audit_langfuse_enabled: bool
    langfuse_audit_sink_enabled: bool
    audit_structured_log_enabled: bool


class HealthResponse(BaseModel):
    """Liveness body plus ``ready`` when required settings validate (hosted readiness probes)."""

    service: Literal["jira-triage"] = "jira-triage"
    ready: bool
    observability: ObservabilityHealth | None = Field(
        default=None,
        description="Present when ``ready`` is true: Langfuse and audit wiring status from env.",
    )


class TriagePostResponse(BaseModel):
    """Synchronous triage outcome: merged recommendation or structured failure."""

    run_id: str = Field(
        min_length=1,
        description="Correlation id for this triage attempt (generated at API ingress).",
    )
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


def _flush_inference_telemetry_if_supported(runner: TriageRunner) -> None:
    """Invoke :meth:`TriageHandler.flush_inference_telemetry` when the runner supports it."""
    flush = getattr(runner, "flush_inference_telemetry", None)
    if callable(flush):
        flush()


def create_app(*, triage_handler_factory: Callable[[], TriageRunner] | None = None) -> FastAPI:
    """Build the FastAPI app. Override ``triage_handler_factory`` in tests."""
    factory: Callable[[], TriageRunner] = triage_handler_factory or build_default_triage_handler

    def get_triage_runner() -> TriageRunner:
        return factory()

    def require_triage_token(
        x_triage_token: str | None = Header(default=None, alias="X-Triage-Token"),
    ) -> None:
        expected_token = os.environ.get("TRIAGE_WEBHOOK_TOKEN", "")
        if (
            not expected_token
            or x_triage_token is None
            or not compare_digest(x_triage_token, expected_token)
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Unauthorized",
            )

    app = FastAPI(title="Jira Triage", version="0.1.0")

    @app.get("/health", response_model=None)
    def health() -> HealthResponse | JSONResponse:
        """Process liveness; ``ready`` is true only when :func:`load_settings` succeeds."""
        try:
            settings = load_settings()
        except Exception:
            return JSONResponse(
                status_code=503,
                content={"service": "jira-triage", "ready": False},
            )
        obs = ObservabilityHealth(**observability_status_summary(settings))
        return HealthResponse(ready=True, observability=obs)

    @app.post("/triage", response_model=TriagePostResponse)
    def accept_triage_trigger(
        body: TriageRequest,
        runner: TriageRunner = Depends(get_triage_runner),
        _: None = Depends(require_triage_token),
    ) -> TriagePostResponse:
        run_id = str(uuid.uuid4())
        outcome = runner.run_sync(
            body.issue_key,
            body.project,
            body.source,
            run_id=run_id,
        ).outcome
        _flush_inference_telemetry_if_supported(runner)
        if isinstance(outcome, TriageFailure):
            return TriagePostResponse(
                run_id=run_id,
                issue_key=body.issue_key,
                project=body.project,
                source=body.source,
                status="failed",
                failure=outcome,
            )
        return TriagePostResponse(
            run_id=run_id,
            issue_key=body.issue_key,
            project=body.project,
            source=body.source,
            status="completed",
            recommendation=outcome,
        )

    app.add_middleware(_DebugInboundTriageBodyMiddleware)
    return app


app = create_app()
