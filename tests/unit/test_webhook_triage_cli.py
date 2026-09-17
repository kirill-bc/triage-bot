"""Unit tests for the dedicated Jira Automation webhook triage CLI."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from webhook_triage_cli import main


@pytest.mark.unit
def test_main_does_not_set_retired_apply_mode_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TRIAGE_JIRA_APPLY_MODE", raising=False)
    observed: dict[str, object] = {}

    def _manual_main(argv: list[str] | None = None) -> int:
        observed["argv"] = argv
        observed["mode"] = os.environ.get("TRIAGE_JIRA_APPLY_MODE")
        return 7

    argv = ["TJC-123", "--auto-apply-escalation"]
    with patch("webhook_triage_cli.triage_manual_cli.main", side_effect=_manual_main):
        result = main(argv)

    assert result == 7
    assert observed["mode"] is None
    assert observed["argv"] == [
        "TJC-123",
        "--auto-apply-escalation",
        "--auto-apply-deescalation",
        "--auto-apply-bug-to-story",
    ]
    assert "TRIAGE_JIRA_APPLY_MODE" not in os.environ


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
