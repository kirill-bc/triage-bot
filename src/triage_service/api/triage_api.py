"""HTTP API surface for triage triggers (MVP: request/response contract only).

The request body carries a ``source`` closed enum so callers identify why triage
was invoked: ``bug_created`` (Jira automation on new bugs), ``priority_changed``
(Jira automation on priority edits), or ``manual_trigger`` (local runner / scripts).
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from hmac import compare_digest
from typing import Any, Literal, NoReturn

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field, field_validator, model_validator
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from typing_extensions import Self

from triage_service.adapters.automation_webhook_executor import (
    REDACTED_URL_MARKER,
    WebhookCallbackConfigError,
    configured_webhook_config_error,
    redacted_webhook_url,
    resolve_webhook_callback_credentials,
    validate_request_webhook_url,
)
from triage_service.core.settings import AppSettings, JiraApplyMode, load_settings
from triage_service.core.triage_fallback import TriageFailure
from triage_service.core.triage_handler import TriageRunner, build_default_triage_handler
from triage_service.core.triage_recommendation_parser import TriageRecommendation
from triage_service.observability.log_payload_guard import preview_bytes_for_log
from triage_service.observability.observability_wiring import observability_status_summary
from triage_service.observability.runtime_logging import (
    HttpAccessLogMiddleware,
    configure_runtime_logging,
)

TriageSource = Literal["bug_created", "priority_changed", "manual_trigger"]
LOGGER = logging.getLogger(__name__)

# A URL slash, whether written as `/`, JSON `\/`, or JSON `\u002f`. Each form is matched
# independently so mixed escaping (``https:/\/host/secret``) cannot bypass redaction.
_JSON_SLASH = rb"(?:/|\\/|\\u002f)"

# Group 1 is scheme plus authority; the unbounded tail (the secret part) is dropped. The
# retained authority also stops at ``%`` so percent-encoded delimiters (``%2F``) cannot smuggle
# an encoded secret path into what looks like a bare host. Authority also stops at ``\``, so
# an escaped slash begins the dropped tail rather than being kept as part of the host.
_URL_WITH_PATH_PATTERN = re.compile(
    rb"(https?:" + _JSON_SLASH + rb"{2}[^/?#%\s\"'\\]*)"
    rb"(?:" + _JSON_SLASH + rb"|[^\s\"'\\])*",
    re.IGNORECASE,
)

# The callback member of an unparsable body: its value may encode slashes or be cut off
# mid-string, so the whole value goes, closing quote optional (truncation).
_CALLBACK_URL_MEMBER_PATTERN = re.compile(
    rb'("jira_automation_webhook_url"\s*:\s*")(?:\\.|[^"\\])*("|\Z)',
    re.DOTALL,
)
_REDACTED_MEMBER_REPLACEMENT = rb"\1" + REDACTED_URL_MARKER.encode("utf-8") + rb"\2"


def triage_inbound_debug_enabled() -> bool:
    """True when ``TRIAGE_DEBUG_INBOUND`` requests raw ``POST /triage`` body logging to stderr."""
    token = os.environ.get("TRIAGE_DEBUG_INBOUND", "").strip().lower()
    return token in ("1", "true", "yes", "on")


def preview_request_body_for_log(body: bytes, *, max_len: int = 8192) -> str:
    """Return a UTF-8 string or repr for logging; truncate very large bodies.

    Every URL in the body is reduced to scheme and host because the Rule B callback path is
    a secret, and the callback member of a body that does not parse loses its value outright.
    Redaction runs before request validation, so it must hold for bodies that are not valid
    JSON and must never raise.
    """
    preview_source = _redact_urls_in_body(_redact_callback_url_field(body))
    return preview_bytes_for_log(preview_source, max_bytes=max_len)


def _redact_callback_url_field(body: bytes) -> bytes:
    """Rewrite ``jira_automation_webhook_url`` to its origin when the body parses as JSON.

    Anything json.loads cannot turn into an object is redacted on the raw bytes instead, since
    the value's encoding is then unknown.
    """
    try:
        parsed = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return _drop_callback_url_member(body)
    if not isinstance(parsed, dict):
        return _drop_callback_url_member(body)
    url = parsed.get("jira_automation_webhook_url")
    if not isinstance(url, str) or not url.strip():
        return body
    parsed["jira_automation_webhook_url"] = redacted_webhook_url(url)
    return json.dumps(parsed, ensure_ascii=False).encode("utf-8")


def _drop_callback_url_member(body: bytes) -> bytes:
    """Replace the whole callback URL value, whatever it encodes, on the raw bytes.

    Without a parse the origin cannot be told apart from the secret path: the value may write
    its slashes as ``\\/`` or ``\\u002f``, or the body may stop mid-value. Keeping nothing is
    the only form that holds for all of them.
    """
    return _CALLBACK_URL_MEMBER_PATTERN.sub(_REDACTED_MEMBER_REPLACEMENT, body)


def _redact_urls_in_body(body: bytes) -> bytes:
    """Strip path, query, and fragment from every URL, whatever shape the body has.

    This is the backstop for the JSON path above: a payload that fails to parse (unescaped
    dynamic value from Jira, truncated body) can still carry the secret callback path, under
    the expected key or any other, with ``/``, ``\\/``, and ``\\u002f`` mixed arbitrarily.
    """
    return _URL_WITH_PATH_PATTERN.sub(rb"\1", body)


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
    jira_apply_mode: JiraApplyMode | None = Field(
        default=None,
        description=(
            "Optional per-request Jira outcome delivery mode. Omit to use "
            "TRIAGE_JIRA_APPLY_MODE from the service environment."
        ),
    )
    jira_automation_webhook_url: str | None = Field(
        default=None,
        description=(
            "Optional callback URL for this project's Automation rule. Must be an https URL on "
            "api-private.atlassian.com and must be sent with X-Jira-Automation-Webhook-Token. "
            "Omit both to use the JIRA_AUTOMATION_WEBHOOK_URL / JIRA_AUTOMATION_WEBHOOK_TOKEN "
            "pair from settings (Secret only; never a ConfigMap)."
        ),
    )

    @field_validator("jira_automation_webhook_url")
    @classmethod
    def _check_automation_webhook_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_request_webhook_url(value)


@dataclass(frozen=True, slots=True)
class _TriageTrigger:
    """Parsed POST /triage body plus the runner built from that same instance."""

    body: TriageRequest
    runner: TriageRunner


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


def _resolve_log_level() -> str:
    token = os.environ.get("LOG_LEVEL", "INFO").strip().upper()
    allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
    return token if token in allowed else "INFO"


def _concurrency_limits_from_settings() -> tuple[int, float]:
    """Return semaphore size and wait timeout from ``AppSettings``.

    ``create_app`` must succeed even when required credentials are missing so
    ``GET /health`` can report ``ready: false``. In that case fall back to the
    same Field defaults as ``AppSettings``.
    """
    default_runs = AppSettings.model_fields["triage_max_concurrent_runs"].default
    default_wait = AppSettings.model_fields["triage_concurrency_wait_seconds"].default
    try:
        settings = load_settings()
    except Exception:
        return int(default_runs), float(default_wait)
    return settings.triage_max_concurrent_runs, settings.triage_concurrency_wait_seconds


def _optional_app_settings() -> AppSettings | None:
    """Return loaded settings, or None when required credentials are not yet valid."""
    try:
        return load_settings()
    except Exception:
        return None


def require_webhook_callback_config(
    body: TriageRequest,
    *,
    request_webhook_token: str | None,
    settings: AppSettings | None = None,
) -> None:
    """Reject webhook mode unless one complete, valid callback credential pair is available.

    Pairing, source precedence, and URL validation live in
    ``resolve_webhook_callback_credentials``; this wrapper maps that typed error to HTTP 422
    with API-facing field names.
    """
    if settings is None:
        settings = _optional_app_settings()
    effective_mode: JiraApplyMode = body.jira_apply_mode or (
        settings.triage_jira_apply_mode if settings is not None else "direct"
    )
    if effective_mode != "automation_webhook":
        return
    try:
        resolve_webhook_callback_credentials(
            request_url=body.jira_automation_webhook_url,
            request_token=request_webhook_token,
            settings=settings,
        )
    except WebhookCallbackConfigError as exc:
        _reject_callback_config(_api_callback_error_detail(exc))


def _api_callback_error_detail(exc: WebhookCallbackConfigError) -> str:
    """Map resolver codes onto HTTP field names without re-implementing pairing rules."""
    if exc.code == "request_url_without_token":
        return (
            "jira_automation_webhook_url requires the matching "
            "X-Jira-Automation-Webhook-Token header: per-request callback credentials must "
            "be sent together"
        )
    if exc.code == "request_token_without_url":
        return (
            "X-Jira-Automation-Webhook-Token requires the matching "
            "jira_automation_webhook_url body field: per-request callback credentials must "
            "be sent together"
        )
    if exc.code == "missing_pair":
        return (
            "jira_apply_mode=automation_webhook requires a callback URL and token: send "
            "jira_automation_webhook_url with X-Jira-Automation-Webhook-Token, or configure "
            "both JIRA_AUTOMATION_WEBHOOK_URL and JIRA_AUTOMATION_WEBHOOK_TOKEN"
        )
    return str(exc)


def _reject_callback_config(detail: str) -> NoReturn:
    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=detail)


def sanitized_validation_errors(exc: RequestValidationError) -> list[dict[str, Any]]:
    """Pydantic error entries with the rejected input values removed.

    FastAPI's default 422 payload echoes each error's ``input``; for a rejected
    ``jira_automation_webhook_url`` that is the full Rule B URL with its secret path, and
    for a missing-field error it is the entire request body (which can carry that URL).
    Keeping only ``type``, ``loc``, and ``msg`` stays debuggable — URL validation messages
    never include the URL — without persisting the credential in clients or proxies.
    """
    return [
        {"type": error.get("type"), "loc": error.get("loc"), "msg": error.get("msg")}
        for error in exc.errors()
    ]


def _run_triage_within_capacity(
    body: TriageRequest,
    runner: TriageRunner,
    *,
    triage_slots: threading.Semaphore,
    concurrency_wait_seconds: float,
    max_concurrent_runs: int,
) -> TriagePostResponse:
    """Acquire a concurrency slot, run triage, and shape the response (or 503 if full)."""
    if not triage_slots.acquire(timeout=concurrency_wait_seconds):
        LOGGER.warning(
            "triage_api_busy issue_key=%s project=%s source=%s "
            "max_concurrent_runs=%d wait_seconds=%s",
            body.issue_key,
            body.project,
            body.source,
            max_concurrent_runs,
            concurrency_wait_seconds,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Triage service is at capacity; retry later.",
        )
    try:
        run_id = str(uuid.uuid4())
        outcome = runner.run_sync(
            body.issue_key,
            body.project,
            body.source,
            run_id=run_id,
        ).outcome
        _flush_inference_telemetry_if_supported(runner)
    finally:
        triage_slots.release()
    if isinstance(outcome, TriageFailure):
        LOGGER.warning(
            "triage_api_failed run_id=%s issue_key=%s project=%s source=%s "
            "category=%s message=%s",
            run_id,
            body.issue_key,
            body.project,
            body.source,
            outcome.category,
            outcome.message,
        )
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


def create_app(*, triage_handler_factory: Callable[[], TriageRunner] | None = None) -> FastAPI:
    """Build the FastAPI app. Override ``triage_handler_factory`` in tests."""
    max_concurrent_runs, concurrency_wait_seconds = _concurrency_limits_from_settings()
    triage_slots = threading.Semaphore(max_concurrent_runs)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        configure_runtime_logging(log_level=_resolve_log_level())
        LOGGER.info("triage_api_started")
        yield

    def get_triage_trigger(
        body: TriageRequest,
        x_jira_automation_webhook_token: str | None = Header(
            default=None,
            alias="X-Jira-Automation-Webhook-Token",
        ),
    ) -> _TriageTrigger:
        require_webhook_callback_config(
            body,
            request_webhook_token=x_jira_automation_webhook_token,
        )
        if triage_handler_factory is not None:
            runner = triage_handler_factory()
        else:
            token = (x_jira_automation_webhook_token or "").strip() or None
            runner = build_default_triage_handler(
                jira_apply_mode=body.jira_apply_mode,
                jira_automation_webhook_token=token,
                jira_automation_webhook_url=body.jira_automation_webhook_url,
            )
        return _TriageTrigger(body=body, runner=runner)

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

    app = FastAPI(title="Jira Triage", version="0.1.0", lifespan=lifespan)

    @app.exception_handler(RequestValidationError)
    async def _validation_error_without_input(
        _request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        """422 without echoed input: the body can carry the secret Rule B callback URL."""
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={"detail": sanitized_validation_errors(exc)},
        )

    @app.get("/health", response_model=None)
    async def health() -> HealthResponse | JSONResponse:
        """Process liveness; ``ready`` is true only when the runtime configuration is usable.

        Usable means :func:`load_settings` succeeds and any configured callback fallback pair
        (``JIRA_AUTOMATION_WEBHOOK_URL`` / ``JIRA_AUTOMATION_WEBHOOK_TOKEN``) is complete and
        valid, so a misconfigured Secret surfaces at the readiness probe instead of after a
        full triage run. Async so readiness probes are served directly on the event loop and
        are never starved by the sync worker thread pool when triage slots are saturated.
        """
        try:
            settings = load_settings()
        except Exception:
            return JSONResponse(
                status_code=503,
                content={"service": "jira-triage", "ready": False},
            )
        callback_config_error = configured_webhook_config_error(settings)
        if callback_config_error is not None:
            # The message never contains the URL itself (its path is a secret).
            LOGGER.warning(
                "triage_api_not_ready invalid callback configuration: %s",
                callback_config_error,
            )
            return JSONResponse(
                status_code=503,
                content={"service": "jira-triage", "ready": False},
            )
        obs = ObservabilityHealth(**observability_status_summary(settings))
        return HealthResponse(ready=True, observability=obs)

    @app.post("/triage", response_model=TriagePostResponse)
    def accept_triage_trigger(
        trigger: _TriageTrigger = Depends(get_triage_trigger),
        _: None = Depends(require_triage_token),
    ) -> TriagePostResponse:
        return _run_triage_within_capacity(
            trigger.body,
            trigger.runner,
            triage_slots=triage_slots,
            concurrency_wait_seconds=concurrency_wait_seconds,
            max_concurrent_runs=max_concurrent_runs,
        )

    app.add_middleware(HttpAccessLogMiddleware)
    app.add_middleware(_DebugInboundTriageBodyMiddleware)
    return app


app = create_app()
