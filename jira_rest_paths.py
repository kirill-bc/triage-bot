"""Jira Cloud REST v3 URL prefix shared by fetcher and triage action executor."""

from __future__ import annotations

from settings import AppSettings

_ATLASSIAN_GATEWAY = "https://api.atlassian.com/ex/jira"


def jira_rest_v3_site_prefix(settings: AppSettings) -> str | None:
    """Return base URL without trailing slash before ``/rest/api/3/...``.

    Prefer ``JIRA_CLOUD_ID`` (``https://api.atlassian.com/ex/jira/{id}``) over
    ``JIRA_BASE_URL`` when both are set.
    """
    cloud_raw = settings.jira_cloud_id
    if cloud_raw is not None and str(cloud_raw).strip():
        cloud_id = str(cloud_raw).strip()
        return f"{_ATLASSIAN_GATEWAY}/{cloud_id}"
    base = settings.jira_base_url
    if base is None or not str(base).strip():
        return None
    return str(base).strip().rstrip("/")
