"""Load application settings from environment and optional `.env` file."""

from __future__ import annotations

from pathlib import Path

from dotenv import find_dotenv, load_dotenv
from pydantic import Field, computed_field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing_extensions import Self


class AppSettings(BaseSettings):
    """Credentials and tuning values for Jira, OpenRouter, and logging."""

    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    jira_api_key: str = Field(min_length=1, description="Jira Cloud API token (or server PAT).")
    openrouter_api_key: str = Field(min_length=1, description="OpenRouter API key.")
    openrouter_model: str = Field(
        default="openai/gpt-4o-mini",
        min_length=1,
        description="OpenRouter model id, e.g. openai/gpt-4o-mini or anthropic/claude-3-haiku.",
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
