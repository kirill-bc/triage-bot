"""Triage runtime config: project allowlist, analysis delay, feature flags."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from core_config import TriageCoreConfig, load_triage_core_config

_TRIAGE_ENV_KEYS = (
    "TRIAGE_ALLOWED_PROJECTS",
    "TRIAGE_ANALYSIS_DELAY_SECONDS",
    "TRIAGE_DEDUPE_DEFERRAL_ENABLED",
)


@pytest.fixture(autouse=True)
def _clear_triage_env_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in _TRIAGE_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


@pytest.mark.unit
def test_load_triage_core_config_default_allowlist_is_tjc_then_bc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    cfg = load_triage_core_config()
    assert cfg.allowed_projects == ["TJC", "BC"]


@pytest.mark.unit
def test_load_triage_core_config_allowed_projects_from_comma_separated_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TRIAGE_ALLOWED_PROJECTS", "BC,TJC")
    cfg = load_triage_core_config()
    assert cfg.allowed_projects == ["BC", "TJC"]


@pytest.mark.unit
def test_load_triage_core_config_allowed_projects_strips_whitespace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TRIAGE_ALLOWED_PROJECTS", " TJC , BC ")
    cfg = load_triage_core_config()
    assert cfg.allowed_projects == ["TJC", "BC"]


@pytest.mark.unit
def test_load_triage_core_config_analysis_delay_defaults_to_five_minutes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    cfg = load_triage_core_config()
    assert cfg.analysis_delay_seconds == 300


@pytest.mark.unit
def test_load_triage_core_config_analysis_delay_from_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TRIAGE_ANALYSIS_DELAY_SECONDS", "120")
    cfg = load_triage_core_config()
    assert cfg.analysis_delay_seconds == 120


@pytest.mark.unit
def test_load_triage_core_config_analysis_delay_rejects_negative(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TRIAGE_ANALYSIS_DELAY_SECONDS", "-1")
    with pytest.raises(ValidationError) as exc:
        load_triage_core_config()
    assert "analysis_delay_seconds" in str(exc.value).lower()


@pytest.mark.unit
def test_load_triage_core_config_dedupe_deferral_defaults_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    cfg = load_triage_core_config()
    assert cfg.dedupe_deferral_enabled is False


@pytest.mark.unit
def test_load_triage_core_config_dedupe_deferral_from_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TRIAGE_DEDUPE_DEFERRAL_ENABLED", "true")
    cfg = load_triage_core_config()
    assert cfg.dedupe_deferral_enabled is True


@pytest.mark.unit
def test_triage_core_config_rejects_empty_allowlist(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TRIAGE_ALLOWED_PROJECTS", "")
    with pytest.raises(ValidationError) as exc:
        load_triage_core_config()
    assert "allowed_projects" in str(exc.value).lower()


@pytest.mark.unit
def test_triage_core_config_reads_from_process_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """When no .env in cwd tree, OS environment is enough."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TRIAGE_ANALYSIS_DELAY_SECONDS", "60")
    cfg = TriageCoreConfig()
    assert cfg.analysis_delay_seconds == 60
