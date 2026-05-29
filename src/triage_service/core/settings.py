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
    triage_text_model: str = Field(
        default="openai/gpt-4o-mini",
        min_length=1,
        validation_alias="TRIAGE_TEXT_MODEL",
        description="OpenRouter model id for classification and priority (text).",
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
    triage_langfuse_prompts_enabled: bool = Field(
        default=True,
        validation_alias="TRIAGE_LANGFUSE_PROMPTS_ENABLED",
        description="Fetch triage prompts from Langfuse when keys are configured.",
    )
    triage_langfuse_prompt_label: str | None = Field(
        default=None,
        validation_alias="TRIAGE_LANGFUSE_PROMPT_LABEL",
        description="Langfuse prompt version label (e.g. production); empty uses SDK default.",
    )
    triage_langfuse_prompt_cache_ttl_seconds: int | None = Field(
        default=None,
        ge=0,
        validation_alias="TRIAGE_LANGFUSE_PROMPT_CACHE_TTL_SECONDS",
        description="Langfuse prompt client cache TTL in seconds; unset uses SDK default.",
    )
    triage_langfuse_reason_for_humans_prompt_name: str = Field(
        default="triagebot/reason-for-humans",
        min_length=1,
        validation_alias="TRIAGE_LANGFUSE_REASON_FOR_HUMANS_PROMPT_NAME",
    )
    triage_langfuse_classification_system_prompt_name: str = Field(
        default="triagebot/classification-system",
        min_length=1,
        validation_alias="TRIAGE_LANGFUSE_CLASSIFICATION_SYSTEM_PROMPT_NAME",
    )
    triage_langfuse_priority_system_prompt_name: str = Field(
        default="triagebot/priority-system",
        min_length=1,
        validation_alias="TRIAGE_LANGFUSE_PRIORITY_SYSTEM_PROMPT_NAME",
    )
    triage_langfuse_classification_prompt_name: str = Field(
        default="triagebot/classification-user",
        min_length=1,
        validation_alias="TRIAGE_LANGFUSE_CLASSIFICATION_PROMPT_NAME",
    )
    triage_langfuse_priority_prompt_name: str = Field(
        default="triagebot/priority-user",
        min_length=1,
        validation_alias="TRIAGE_LANGFUSE_PRIORITY_PROMPT_NAME",
    )
    triage_langfuse_vision_system_prompt_name: str = Field(
        default="triagebot/vision-system",
        min_length=1,
        validation_alias="TRIAGE_LANGFUSE_VISION_SYSTEM_PROMPT_NAME",
    )
    triage_langfuse_vision_user_prompt_name: str = Field(
        default="triagebot/vision-user",
        min_length=1,
        validation_alias="TRIAGE_LANGFUSE_VISION_USER_PROMPT_NAME",
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
        default=False,
        validation_alias="TRIAGE_AUDIT_REDACT_MODEL_INPUT",
        description=(
            "Redact model input (prompts) in Langfuse generation traces "
            "(default false for prompt debugging visibility)."
        ),
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

    triage_image_context_enabled: bool = Field(
        default=False,
        validation_alias="TRIAGE_IMAGE_CONTEXT_ENABLED",
        description="Extract image attachment text before classification and priority.",
    )
    triage_vision_model: str = Field(
        default="google/gemini-2.0-flash-001",
        min_length=1,
        validation_alias="TRIAGE_VISION_MODEL",
        description="OpenRouter vision model for screenshot transcription (not TRIAGE_TEXT_MODEL).",
    )
    triage_image_context_max_attachments: int = Field(
        default=5,
        ge=1,
        le=20,
        validation_alias="TRIAGE_IMAGE_CONTEXT_MAX_ATTACHMENTS",
        description="Max image attachments to send to vision per issue.",
    )
    triage_image_context_max_bytes_per_image: int = Field(
        default=5 * 1024 * 1024,
        ge=1,
        le=20 * 1024 * 1024,
        validation_alias="TRIAGE_IMAGE_CONTEXT_MAX_BYTES_PER_IMAGE",
        description="Skip vision when attachment binary exceeds this size (bytes).",
    )
    triage_image_context_timeout_seconds: float = Field(
        default=90.0,
        ge=1.0,
        le=300.0,
        validation_alias="TRIAGE_IMAGE_CONTEXT_TIMEOUT_SECONDS",
        description="Per-attempt HTTP timeout for OpenRouter vision calls.",
    )
    triage_audit_redact_image_transcript: bool = Field(
        default=True,
        validation_alias="TRIAGE_AUDIT_REDACT_IMAGE_TRANSCRIPT",
        description=(
            "Redact vision model output (TRANSCRIPT/SUMMARY) in Langfuse inference_vision "
            "generations. Jira description and repro in vision inputs are always kept."
        ),
    )
    triage_auto_apply_deescalation: bool = Field(
        default=False,
        validation_alias="TRIAGE_AUTO_APPLY_DEESCALATION",
        description=(
            "Auto-apply Jira priority field changes when TriageBot recommends a less urgent "
            "priority (deescalation)."
        ),
    )
    triage_auto_apply_bug_to_story: bool = Field(
        default=False,
        validation_alias="TRIAGE_AUTO_APPLY_BUG_TO_STORY",
        description=(
            "Auto-apply Jira issue-type update from Bug to Story when Story recommendation "
            "mismatches current issue type."
        ),
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

    @property
    def langfuse_prompt_management_enabled(self) -> bool:
        """True when Langfuse prompt fetch is on and both API keys are configured."""
        if not self.triage_langfuse_prompts_enabled:
            return False
        return bool(str(self.langfuse_public_key or "").strip()) and bool(
            str(self.langfuse_secret_key or "").strip(),
        )

    @field_validator("triage_langfuse_prompt_label", mode="before")
    @classmethod
    def _empty_langfuse_prompt_label_to_none(cls, value: object) -> object:
        if value is None:
            return None
        token = str(value).strip()
        return token or None

    @field_validator("triage_langfuse_prompt_cache_ttl_seconds", mode="before")
    @classmethod
    def _empty_langfuse_prompt_cache_ttl_to_none(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return value

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
