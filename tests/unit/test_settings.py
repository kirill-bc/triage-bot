"""Environment-backed application settings."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from triage_service.core.settings import AppSettings, load_settings


@pytest.mark.unit
def test_load_settings_raises_when_jira_api_key_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "OPENROUTER_API_KEY=or-secret\nTRIAGE_WEBHOOK_TOKEN=triage-token\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError) as exc:
        load_settings()
    assert "jira_api_key" in str(exc.value).lower()


@pytest.mark.unit
def test_load_settings_raises_when_openrouter_api_key_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "JIRA_API_KEY=jira-secret\nTRIAGE_WEBHOOK_TOKEN=triage-token\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError) as exc:
        load_settings()
    assert "openrouter_api_key" in str(exc.value).lower()


@pytest.mark.unit
def test_load_settings_raises_on_empty_required_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "JIRA_API_KEY=\nOPENROUTER_API_KEY=x\nTRIAGE_WEBHOOK_TOKEN=triage-token\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        load_settings()


@pytest.mark.unit
def test_load_settings_loads_required_keys_from_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "JIRA_API_KEY=jira-token\n"
        "OPENROUTER_API_KEY=or-token\n"
        "TRIAGE_WEBHOOK_TOKEN=triage-token\n"
        "TRIAGE_TEXT_MODEL=openai/gpt-4o\n"
        "JIRA_CLOUD_ID=abc-123-cloud\n"
        "JIRA_USER_EMAIL=triage@example.com\n"
        "LOG_LEVEL=DEBUG\n"
        "LOGGING_API_KEY=log-secret\n"
        "LOGGING_ENDPOINT=https://logs.example/ingest\n",
        encoding="utf-8",
    )
    settings = load_settings()
    assert settings.jira_api_key == "jira-token"
    assert settings.openrouter_api_key == "or-token"
    assert settings.triage_webhook_token == "triage-token"
    assert settings.triage_text_model == "openai/gpt-4o"
    assert settings.jira_cloud_id == "abc-123-cloud"
    assert settings.jira_user_email == "triage@example.com"
    assert settings.log_level == "DEBUG"
    assert settings.logging_api_key == "log-secret"
    assert settings.logging_endpoint == "https://logs.example/ingest"


@pytest.mark.unit
def test_load_settings_optional_fields_default_when_omitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "JIRA_API_KEY=jira-token\nOPENROUTER_API_KEY=or-token\nTRIAGE_WEBHOOK_TOKEN=triage-token\n",
        encoding="utf-8",
    )
    settings = load_settings()
    assert settings.triage_text_model == "openai/gpt-4o-mini"
    assert settings.jira_cloud_id is None
    assert settings.jira_user_email is None
    assert settings.log_level == "INFO"
    assert settings.logging_api_key is None
    assert settings.logging_endpoint is None
    assert settings.audit_structured_log_enabled is True
    assert settings.audit_langfuse_enabled is True
    assert settings.audit_redact_model_input is False
    assert settings.audit_redact_model_output is False
    assert settings.openrouter_http_timeout_seconds == 60.0
    assert settings.openrouter_http_max_retries == 2


@pytest.mark.unit
def test_load_settings_reads_audit_feature_flags_and_redaction_toggles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "JIRA_API_KEY=jira-token\n"
        "OPENROUTER_API_KEY=or-token\n"
        "TRIAGE_WEBHOOK_TOKEN=triage-token\n"
        "TRIAGE_AUDIT_STRUCTURED_LOG_ENABLED=false\n"
        "TRIAGE_AUDIT_LANGFUSE_ENABLED=false\n"
        "TRIAGE_AUDIT_REDACT_MODEL_INPUT=true\n"
        "TRIAGE_AUDIT_REDACT_MODEL_OUTPUT=false\n",
        encoding="utf-8",
    )
    settings = load_settings()
    assert settings.audit_structured_log_enabled is False
    assert settings.audit_langfuse_enabled is False
    assert settings.audit_redact_model_input is True
    assert settings.audit_redact_model_output is False


@pytest.mark.unit
def test_load_settings_reads_jira_http_timeout_and_max_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "JIRA_API_KEY=jira-token\n"
        "OPENROUTER_API_KEY=or-token\n"
        "TRIAGE_WEBHOOK_TOKEN=triage-token\n"
        "TRIAGE_JIRA_HTTP_TIMEOUT_SECONDS=45\n"
        "TRIAGE_JIRA_HTTP_MAX_RETRIES=0\n",
        encoding="utf-8",
    )
    settings = load_settings()
    assert settings.jira_http_timeout_seconds == 45.0
    assert settings.jira_http_max_retries == 0


@pytest.mark.unit
def test_load_settings_reads_openrouter_http_timeout_and_max_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "JIRA_API_KEY=jira-token\n"
        "OPENROUTER_API_KEY=or-token\n"
        "TRIAGE_WEBHOOK_TOKEN=triage-token\n"
        "TRIAGE_OPENROUTER_HTTP_TIMEOUT_SECONDS=90\n"
        "TRIAGE_OPENROUTER_HTTP_MAX_RETRIES=1\n",
        encoding="utf-8",
    )
    settings = load_settings()
    assert settings.openrouter_http_timeout_seconds == 90.0
    assert settings.openrouter_http_max_retries == 1


@pytest.mark.unit
def test_app_settings_reads_from_process_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """When no .env in cwd tree, OS environment is enough."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JIRA_API_KEY", "from-env")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-from-env")
    monkeypatch.setenv("TRIAGE_WEBHOOK_TOKEN", "triage-token")
    settings = AppSettings()
    assert settings.jira_api_key == "from-env"
    assert settings.openrouter_api_key == "or-from-env"


@pytest.mark.unit
def test_app_settings_strips_outer_quotes_from_langfuse_base_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JIRA_API_KEY", "from-env")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-from-env")
    monkeypatch.setenv("TRIAGE_WEBHOOK_TOKEN", "triage-token")
    monkeypatch.setenv("LANGFUSE_BASE_URL", '"https://us.cloud.langfuse.com"')
    settings = AppSettings()
    assert settings.langfuse_base_url == "https://us.cloud.langfuse.com"


@pytest.mark.unit
def test_load_settings_default_allowlist_is_tjc_then_bc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "JIRA_API_KEY=jira-token\nOPENROUTER_API_KEY=or-token\nTRIAGE_WEBHOOK_TOKEN=triage-token\n",
        encoding="utf-8",
    )
    settings = load_settings()
    assert settings.allowed_projects == ["TJC", "BC"]


@pytest.mark.unit
def test_load_settings_allowlist_from_comma_separated_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "JIRA_API_KEY=jira-token\n"
        "OPENROUTER_API_KEY=or-token\n"
        "TRIAGE_WEBHOOK_TOKEN=triage-token\n"
        "TRIAGE_ALLOWED_PROJECTS= BC , TJC \n",
        encoding="utf-8",
    )
    settings = load_settings()
    assert settings.allowed_projects == ["BC", "TJC"]


@pytest.mark.unit
def test_load_settings_rejects_empty_allowlist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "JIRA_API_KEY=jira-token\n"
        "OPENROUTER_API_KEY=or-token\n"
        "TRIAGE_WEBHOOK_TOKEN=triage-token\n"
        "TRIAGE_ALLOWED_PROJECTS=\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError) as exc:
        load_settings()
    assert "allowed_projects" in str(exc.value).lower()


@pytest.mark.unit
def test_load_settings_ignores_retired_delay_and_dedupe_env_vars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "JIRA_API_KEY=jira-token\nOPENROUTER_API_KEY=or-token\nTRIAGE_WEBHOOK_TOKEN=triage-token\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("TRIAGE_ANALYSIS_DELAY_SECONDS", "120")
    monkeypatch.setenv("TRIAGE_DEDUPE_DEFERRAL_ENABLED", "true")
    settings = load_settings()
    assert settings.allowed_projects == ["TJC", "BC"]
    assert not hasattr(settings, "analysis_delay_seconds")
    assert not hasattr(settings, "dedupe_deferral_enabled")


@pytest.mark.unit
def test_load_settings_raises_when_triage_webhook_token_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "JIRA_API_KEY=jira-token\nOPENROUTER_API_KEY=or-token\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError) as exc:
        load_settings()
    assert "triage_webhook_token" in str(exc.value).lower()
