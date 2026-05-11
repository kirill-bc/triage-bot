"""Environment-backed application settings."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from settings import AppSettings, load_settings

_APP_ENV_KEYS = (
    "JIRA_API_KEY",
    "OPENROUTER_API_KEY",
    "JIRA_BASE_URL",
    "JIRA_USER_EMAIL",
    "LOG_LEVEL",
    "LOGGING_API_KEY",
    "LOGGING_ENDPOINT",
)


@pytest.fixture(autouse=True)
def _clear_app_env_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dotenv uses override=False; strip prior test / shell values before each case."""
    for key in _APP_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


@pytest.mark.unit
def test_load_settings_raises_when_jira_api_key_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("OPENROUTER_API_KEY=or-secret\n", encoding="utf-8")
    with pytest.raises(ValidationError) as exc:
        load_settings()
    assert "jira_api_key" in str(exc.value).lower()


@pytest.mark.unit
def test_load_settings_raises_when_openrouter_api_key_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("JIRA_API_KEY=jira-secret\n", encoding="utf-8")
    with pytest.raises(ValidationError) as exc:
        load_settings()
    assert "openrouter_api_key" in str(exc.value).lower()


@pytest.mark.unit
def test_load_settings_raises_on_empty_required_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "JIRA_API_KEY=\nOPENROUTER_API_KEY=x\n",
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
        "JIRA_BASE_URL=https://example.atlassian.net\n"
        "JIRA_USER_EMAIL=triage@example.com\n"
        "LOG_LEVEL=DEBUG\n"
        "LOGGING_API_KEY=log-secret\n"
        "LOGGING_ENDPOINT=https://logs.example/ingest\n",
        encoding="utf-8",
    )
    settings = load_settings()
    assert settings.jira_api_key == "jira-token"
    assert settings.openrouter_api_key == "or-token"
    assert settings.jira_base_url == "https://example.atlassian.net"
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
        "JIRA_API_KEY=jira-token\nOPENROUTER_API_KEY=or-token\n",
        encoding="utf-8",
    )
    settings = load_settings()
    assert settings.jira_base_url is None
    assert settings.jira_user_email is None
    assert settings.log_level == "INFO"
    assert settings.logging_api_key is None
    assert settings.logging_endpoint is None


@pytest.mark.unit
def test_app_settings_reads_from_process_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """When no .env in cwd tree, OS environment is enough."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JIRA_API_KEY", "from-env")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-from-env")
    settings = AppSettings()
    assert settings.jira_api_key == "from-env"
    assert settings.openrouter_api_key == "or-from-env"
