"""Load application settings from environment and optional `.env` file."""

from __future__ import annotations

from pathlib import Path

from dotenv import find_dotenv, load_dotenv
from pydantic import Field, computed_field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing_extensions import Self


def _strip_matching_quotes(value: str) -> str:
    """Drop one matching outer quote pair (Docker --env-file keeps quote chars)."""
    trimmed = value.strip()
    if len(trimmed) >= 2 and trimmed[0] == trimmed[-1] and trimmed[0] in ("'", '"'):
        return trimmed[1:-1].strip()
    return trimmed


class AppSettings(BaseSettings):
    """Credentials and tuning values for Jira, OpenRouter, and logging."""

    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    jira_api_key: str = Field(min_length=1, description="Jira Cloud API token (or server PAT).")
    openrouter_api_key: str = Field(min_length=1, description="OpenRouter API key.")
    triage_webhook_token: str = Field(
        min_length=1,
        validation_alias="TRIAGE_WEBHOOK_TOKEN",
        description="Shared secret required in X-Triage-Token for POST /triage requests.",
    )
    openrouter_model: str = Field(
        default="openai/gpt-4o-mini",
        min_length=1,
        description="OpenRouter model id, e.g. openai/gpt-4o-mini or anthropic/claude-3-haiku.",
    )

    langfuse_public_key: str | None = Field(
        default=None,
        validation_alias="LANGFUSE_PUBLIC_KEY",
        description="LangFuse public key; omit to disable LangFuse inference tracing.",
    )
    langfuse_secret_key: str | None = Field(
        default=None,
        validation_alias="LANGFUSE_SECRET_KEY",
        description="LangFuse secret key; both keys required to enable tracing.",
    )
    langfuse_base_url: str | None = Field(
        default=None,
        validation_alias="LANGFUSE_BASE_URL",
        description="LangFuse API base URL (e.g. https://cloud.langfuse.com); optional.",
    )
    audit_structured_log_enabled: bool = Field(
        default=True,
        validation_alias="TRIAGE_AUDIT_STRUCTURED_LOG_ENABLED",
        description="Enable structured JSON audit logs for triage lifecycle events.",
    )
    audit_langfuse_enabled: bool = Field(
        default=True,
        validation_alias="TRIAGE_AUDIT_LANGFUSE_ENABLED",
        description="Enable LangFuse audit sink when credentials are configured.",
    )
    audit_redact_model_input: bool = Field(
        default=True,
        validation_alias="TRIAGE_AUDIT_REDACT_MODEL_INPUT",
        description="Redact model input payloads before audit persistence.",
    )
    audit_redact_model_output: bool = Field(
        default=False,
        validation_alias="TRIAGE_AUDIT_REDACT_MODEL_OUTPUT",
        description=(
            "Redact model output payloads before audit persistence "
            "(default false for internal debugging visibility)."
        ),
    )

    jira_cloud_id: str | None = Field(
        default=None,
        description=(
            "Atlassian Cloud site id for api.atlassian.com REST (ex/jira/CLOUD_ID/...). "
            "Issue fetch and triage Jira actions require this gateway id."
        ),
    )
    jira_user_email: str | None = Field(
        default=None,
        description="Atlassian account email paired with jira_api_key for Cloud REST auth.",
    )
    jira_reproduction_steps_field_id: str | None = Field(
        default="customfield_10251",
        validation_alias="TRIAGE_JIRA_REPRODUCTION_STEPS_FIELD_ID",
        description=(
            "Optional Jira field id holding reproduction steps (e.g. customfield_10251). "
            "When set, issue fetch requests this field and prefers it over description parsing."
        ),
    )
    jira_http_timeout_seconds: float = Field(
        default=30.0,
        ge=1.0,
        le=300.0,
        validation_alias="TRIAGE_JIRA_HTTP_TIMEOUT_SECONDS",
        description="Per-attempt timeout (seconds) for Jira REST fetch and write calls.",
    )
    jira_http_max_retries: int = Field(
        default=2,
        ge=0,
        le=10,
        validation_alias="TRIAGE_JIRA_HTTP_MAX_RETRIES",
        description="Extra attempts after first transient failure (429/502/503/504 or transport).",
    )
    openrouter_http_timeout_seconds: float = Field(
        default=60.0,
        ge=1.0,
        le=300.0,
        validation_alias="TRIAGE_OPENROUTER_HTTP_TIMEOUT_SECONDS",
        description="Per-attempt timeout (seconds) for OpenRouter chat completion calls.",
    )
    openrouter_http_max_retries: int = Field(
        default=2,
        ge=0,
        le=10,
        validation_alias="TRIAGE_OPENROUTER_HTTP_MAX_RETRIES",
        description="Extra attempts after first transient failure (429/502/503/504 or transport).",
    )

    log_level: str = Field(default="INFO", description="Standard library log level name.")
    logging_api_key: str | None = Field(
        default=None,
        description="Optional API key for a remote logging or observability endpoint.",
    )
    logging_endpoint: str | None = Field(
        default=None,
        description="Optional logging ingest URL when using a hosted log pipeline.",
    )
    # Keep explicit alias so this env var remains stable if settings naming evolves.
    allowed_projects_csv: str = Field(
        default="TJC,BC",
        validation_alias="TRIAGE_ALLOWED_PROJECTS",
        description="Comma-separated Jira project keys eligible for triage.",
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def allowed_projects(self) -> list[str]:
        return [p.strip() for p in self.allowed_projects_csv.split(",") if p.strip()]

    @field_validator("log_level")
    @classmethod
    def log_level_upper(cls, value: str) -> str:
        upper = value.strip().upper()
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if upper not in allowed:
            raise ValueError(f"log_level must be one of {sorted(allowed)}")
        return upper

    @field_validator("langfuse_base_url", mode="before")
    @classmethod
    def normalize_langfuse_base_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = _strip_matching_quotes(str(value))
        return normalized or None

    @model_validator(mode="after")
    def allowed_projects_nonempty(self) -> Self:
        if not self.allowed_projects:
            raise ValueError("allowed_projects must include at least one project key")
        return self


def load_settings(*, env_file: str | Path | None = None) -> AppSettings:
    """Populate process env from `.env` (if present), then return validated settings."""
    path = env_file if env_file is not None else find_dotenv(usecwd=True)
    if path:
        load_dotenv(path, override=False)
    return AppSettings()
