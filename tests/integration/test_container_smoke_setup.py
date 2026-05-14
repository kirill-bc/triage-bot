"""Container smoke tooling is present for local deployment checks."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.mark.integration
def test_repo_has_local_dockerfile_for_service_container() -> None:
    dockerfile = Path("Dockerfile")
    assert dockerfile.exists()
    content = dockerfile.read_text(encoding="utf-8")
    assert "FROM python:" in content
    assert "triage_service.api.triage_api:app" in content


@pytest.mark.integration
def test_repo_has_local_container_smoke_command_script() -> None:
    smoke_script = Path("scripts/run_container_smoke.sh")
    assert smoke_script.exists()
    content = smoke_script.read_text(encoding="utf-8")
    assert "TRIAGE_LOCAL_MOCK_MODE=1" in content
    assert "POST /triage" in content or "/triage" in content


@pytest.mark.integration
def test_repo_has_container_tunnel_command_script_for_jira_automation() -> None:
    tunnel_script = Path("scripts/run_container_tunnel.sh")
    assert tunnel_script.exists()
    content = tunnel_script.read_text(encoding="utf-8")
    assert "cloudflared" in content
    assert "docker run" in content
    assert "/triage" in content
    assert "GET /health" in content or "/health" in content


@pytest.mark.integration
def test_live_container_tunnel_script_checks_run_id_in_container_logs() -> None:
    tunnel_script = Path("scripts/run_container_tunnel.sh")
    content = tunnel_script.read_text(encoding="utf-8")
    assert "TRIAGE_LOCAL_MOCK_MODE=0" in content
    assert "run_id=" in content
    assert "docker logs" in content


@pytest.mark.integration
def test_live_container_tunnel_script_enforces_tjc_only_smoke_scope() -> None:
    tunnel_script = Path("scripts/run_container_tunnel.sh")
    content = tunnel_script.read_text(encoding="utf-8")
    assert "Smoke scope is restricted to TJC project only." in content
    assert 'if [[ "${project}" != "TJC" ]]; then' in content
    assert 'if [[ "${issue_key}" != TJC-* ]]; then' in content


@pytest.mark.integration
def test_live_container_tunnel_script_includes_explicit_operator_guardrails() -> None:
    tunnel_script = Path("scripts/run_container_tunnel.sh")
    content = tunnel_script.read_text(encoding="utf-8")
    assert "Live Jira smoke pre-checks (required):" in content
    assert "LIVE_SMOKE_CONFIRM=YES" in content
    assert "Post-run Jira verification checklist:" in content
    assert "triagebot-reviewed" in content
    assert "triagebot-likely-story" in content
    assert "triagebot-priority-mismatch" in content
