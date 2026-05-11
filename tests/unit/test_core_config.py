"""Triage runtime config: project allowlist only.

The stabilization delay and dedupe flag previously held here were retired when
the integration model shifted to a Jira-side scheduled JQL rule: stabilization
is enforced by the JQL (``created <= -5m``) and dedupe by the ``ai-reviewed``
label filter, so neither setting is meaningful service-side anymore.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from core_config import TriageCoreConfig, load_triage_core_config

_TRIAGE_ENV_KEYS = ("TRIAGE_ALLOWED_PROJECTS",)


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
    monkeypatch.setenv("TRIAGE_ALLOWED_PROJECTS", "BC")
    cfg = TriageCoreConfig()
    assert cfg.allowed_projects == ["BC"]


@pytest.mark.unit
def test_triage_core_config_ignores_retired_delay_and_dedupe_env_vars(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Retired knobs in someone's .env must not crash config loading."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TRIAGE_ANALYSIS_DELAY_SECONDS", "120")
    monkeypatch.setenv("TRIAGE_DEDUPE_DEFERRAL_ENABLED", "true")
    cfg = load_triage_core_config()
    assert cfg.allowed_projects == ["TJC", "BC"]
    assert not hasattr(cfg, "analysis_delay_seconds")
    assert not hasattr(cfg, "dedupe_deferral_enabled")
