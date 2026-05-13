"""Unit tests for shared Jira REST v3 site / gateway prefix."""

from __future__ import annotations

import pytest

from jira_rest_paths import jira_rest_v3_site_prefix
from settings import AppSettings


@pytest.mark.unit
def test_jira_rest_v3_site_prefix_prefers_cloud_id_over_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JIRA_API_KEY", "jira-api-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-token")
    monkeypatch.setenv("JIRA_CLOUD_ID", "cloud-1")
    monkeypatch.setenv("JIRA_BASE_URL", "https://legacy.example.atlassian.net")
    settings = AppSettings()
    assert jira_rest_v3_site_prefix(settings) == "https://api.atlassian.com/ex/jira/cloud-1"


@pytest.mark.unit
def test_jira_rest_v3_site_prefix_uses_base_url_when_cloud_id_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("JIRA_CLOUD_ID", raising=False)
    monkeypatch.setenv("JIRA_API_KEY", "jira-api-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-token")
    monkeypatch.setenv("JIRA_BASE_URL", "https://tenant.atlassian.net/")
    settings = AppSettings()
    assert jira_rest_v3_site_prefix(settings) == "https://tenant.atlassian.net"


@pytest.mark.unit
def test_jira_rest_v3_site_prefix_returns_none_without_cloud_or_base(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("JIRA_CLOUD_ID", raising=False)
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.setenv("JIRA_API_KEY", "jira-api-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-token")
    settings = AppSettings()
    assert jira_rest_v3_site_prefix(settings) is None
