"""Shared pytest configuration and fixtures."""

from __future__ import annotations

import pytest

# Env vars that affect AppSettings; cleared before each test so dotenv/load_settings
# cases in one module do not leak into others (monkeypatch restores prior values on teardown).
_APP_ENV_KEYS = (
    "JIRA_API_KEY",
    "OPENROUTER_API_KEY",
    "OPENROUTER_MODEL",  # retired; clear so shell/.env leftovers do not confuse tests
    "TRIAGE_TEXT_MODEL",
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    "LANGFUSE_BASE_URL",
    "JIRA_CLOUD_ID",
    "JIRA_USER_EMAIL",
    "LOG_LEVEL",
    "LOGGING_API_KEY",
    "LOGGING_ENDPOINT",
    "TRIAGE_ALLOWED_PROJECTS",
    "TRIAGE_ANALYSIS_DELAY_SECONDS",
    "TRIAGE_DEDUPE_DEFERRAL_ENABLED",
    "TRIAGE_AUDIT_STRUCTURED_LOG_ENABLED",
    "TRIAGE_AUDIT_LANGFUSE_ENABLED",
    "TRIAGE_AUDIT_REDACT_MODEL_INPUT",
    "TRIAGE_AUDIT_REDACT_MODEL_OUTPUT",
    "TRIAGE_OPENROUTER_HTTP_TIMEOUT_SECONDS",
    "TRIAGE_OPENROUTER_HTTP_MAX_RETRIES",
    "TRIAGE_WEBHOOK_TOKEN",
    "TRIAGE_IMAGE_CONTEXT_ENABLED",
    "TRIAGE_VISION_MODEL",
)


@pytest.fixture(autouse=True)
def _clear_app_env_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip prior test / shell / dotenv values before each case."""
    for key in _APP_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
