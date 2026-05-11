"""Triage behavior: allowed Jira projects.

The Jira-side scheduled JQL rule owns stabilization (``created <= -5m``) and
dedupe (``labels not in (ai-reviewed)``), so the service no longer carries
``analysis_delay_seconds`` or ``dedupe_deferral_enabled`` config. The project
allowlist stays as a server-side safety net in case a misconfigured Jira rule
sends issues from a project we do not intend to triage.
"""

from __future__ import annotations

from pathlib import Path
from typing_extensions import Self

from dotenv import find_dotenv, load_dotenv
from pydantic import Field, computed_field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class TriageCoreConfig(BaseSettings):
    """Non-secret triage limits: which projects to run on."""

    model_config = SettingsConfigDict(
        env_prefix="TRIAGE_",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Plain str so pydantic-settings doesn't try to JSON-decode it.
    # validation_alias bypasses the env_prefix and reads the full env var name.
    allowed_projects_csv: str = Field(
        default="TJC,BC",
        validation_alias="TRIAGE_ALLOWED_PROJECTS",
        description="Comma-separated Jira project keys eligible for triage.",
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def allowed_projects(self) -> list[str]:
        return [p.strip() for p in self.allowed_projects_csv.split(",") if p.strip()]

    @model_validator(mode="after")
    def allowed_projects_nonempty(self) -> Self:
        if not self.allowed_projects:
            raise ValueError("allowed_projects must include at least one project key")
        return self


def load_triage_core_config(*, env_file: str | Path | None = None) -> TriageCoreConfig:
    """Populate process env from `.env` (if present), then return validated triage config."""
    path = env_file if env_file is not None else find_dotenv(usecwd=True)
    if path:
        load_dotenv(path, override=False)
    return TriageCoreConfig()
