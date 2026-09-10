"""Unit tests for the dedicated Jira Automation webhook triage CLI."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from webhook_triage_cli import main


@pytest.mark.unit
def test_main_forces_automation_webhook_mode_and_forwards_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRIAGE_JIRA_APPLY_MODE", "direct")
    observed: dict[str, object] = {}

    def _manual_main(argv: list[str] | None = None) -> int:
        observed["argv"] = argv
        observed["mode"] = os.environ.get("TRIAGE_JIRA_APPLY_MODE")
        return 7

    argv = ["TJC-123", "--auto-apply-escalation"]
    with patch("webhook_triage_cli.triage_manual_cli.main", side_effect=_manual_main):
        result = main(argv)

    assert result == 7
    assert observed["mode"] == "automation_webhook"
    assert observed["argv"] == [
        "TJC-123",
        "--auto-apply-escalation",
        "--auto-apply-deescalation",
        "--auto-apply-bug-to-story",
    ]
    assert os.environ["TRIAGE_JIRA_APPLY_MODE"] == "direct"


@pytest.mark.unit
def test_main_enables_all_mutation_directives_by_default() -> None:
    observed: dict[str, object] = {}

    def _manual_main(argv: list[str] | None = None) -> int:
        observed["argv"] = argv
        return 0

    with patch("webhook_triage_cli.triage_manual_cli.main", side_effect=_manual_main):
        result = main(["TJC-123"])

    assert result == 0
    assert observed["argv"] == [
        "TJC-123",
        "--auto-apply-deescalation",
        "--auto-apply-escalation",
        "--auto-apply-bug-to-story",
    ]


@pytest.mark.unit
def test_main_restores_unset_apply_mode_after_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TRIAGE_JIRA_APPLY_MODE", raising=False)

    with patch("webhook_triage_cli.triage_manual_cli.main", return_value=0):
        result = main(["TJC-123"])

    assert result == 0
    assert "TRIAGE_JIRA_APPLY_MODE" not in os.environ


@pytest.mark.unit
def test_main_restores_apply_mode_when_manual_cli_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRIAGE_JIRA_APPLY_MODE", "direct")

    with (
        patch(
            "webhook_triage_cli.triage_manual_cli.main",
            side_effect=RuntimeError("boom"),
        ),
        pytest.raises(RuntimeError, match="boom"),
    ):
        main(["TJC-123"])

    assert os.environ["TRIAGE_JIRA_APPLY_MODE"] == "direct"
