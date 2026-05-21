"""Bulk triage CLI."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest

from triage_service.adapters.jira_jql_search import JiraSearchIssueRef
from triage_service.core.settings import AppSettings
from triage_service.core.triage_fallback import fallback_for_exception
from triage_service.core.triage_handler import TriageSyncResult
from triage_service.core.triage_recommendation_parser import TriageRecommendation


@pytest.mark.unit
def test_run_bulk_triage_builds_completed_and_failed_rows() -> None:
    from triage_bulk_cli import run_bulk_triage

    refs = [
        JiraSearchIssueRef(issue_key="TJC-1", issue_type="Bug", priority="P3"),
        JiraSearchIssueRef(issue_key="TJC-2", issue_type="Bug", priority="P1"),
    ]

    class _Runner:
        def __init__(self) -> None:
            self._calls = 0

        def run_sync(
            self,
            issue_key: str,
            project: str,
            source: str,
            *,
            run_id: str,
        ) -> TriageSyncResult:
            _ = (project, source, run_id)
            self._calls += 1
            if issue_key == "TJC-1":
                return TriageSyncResult(
                    outcome=TriageRecommendation(
                        recommended_issue_type="Story",
                        recommended_priority=None,
                        confidence=0.9,
                        reason="Product work.",
                    ),
                )
            return TriageSyncResult(outcome=fallback_for_exception(RuntimeError("boom")))

        def flush_inference_telemetry(self) -> None:
            return None

    class _Settings:
        triage_image_context_enabled = False

    rows = run_bulk_triage(
        refs,
        settings=cast(AppSettings, _Settings()),
        runner=_Runner(),
        show_progress=False,
    )
    assert len(rows) == 2
    assert rows[0].status == "completed"
    assert rows[0].current_priority == "P3"
    assert rows[0].recommendation is not None
    assert rows[0].recommendation["recommended_issue_type"] == "Story"
    assert rows[1].status == "failed"
    assert rows[1].failure is not None


@pytest.mark.unit
def test_main_writes_json_report(tmp_path: Path) -> None:
    from triage_bulk_cli import main

    refs = [
        JiraSearchIssueRef(issue_key="TJC-7", issue_type="Bug", priority="P2"),
    ]

    class _Runner:
        def run_sync(
            self,
            issue_key: str,
            project: str,
            source: str,
            *,
            run_id: str,
        ) -> TriageSyncResult:
            _ = (issue_key, project, source, run_id)
            return TriageSyncResult(
                outcome=TriageRecommendation(
                    recommended_issue_type="Bug",
                    recommended_priority="P0",
                    confidence=0.8,
                    reason="Critical defect.",
                ),
            )

        def flush_inference_telemetry(self) -> None:
            return None

    class _Settings:
        triage_image_context_enabled = False

    out_file = tmp_path / "report.json"
    with (
        patch("triage_service.core.settings.load_settings", return_value=_Settings()),
        patch("triage_bulk_cli.search_issues_by_jql", return_value=refs),
        patch("triage_bulk_cli.build_default_triage_handler", return_value=_Runner()),
    ):
        rc = main(
            [
                "--jql",
                "project = TJC",
                "-o",
                str(out_file),
                "--max-results",
                "1",
            ],
        )

    assert rc == 0
    payload = json.loads(out_file.read_text(encoding="utf-8"))
    assert payload["jql"] == "project = TJC"
    assert payload["apply_to_jira"] is False
    assert payload["issue_count"] == 1
    row = payload["results"][0]
    assert row["issue_key"] == "TJC-7"
    assert row["current_priority"] == "P2"
    assert row["recommendation"]["recommended_priority"] == "P0"


@pytest.mark.unit
def test_main_returns_1_when_jql_matches_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    from triage_bulk_cli import main

    class _Settings:
        triage_image_context_enabled = False

    with (
        patch("triage_service.core.settings.load_settings", return_value=_Settings()),
        patch("triage_bulk_cli.search_issues_by_jql", return_value=[]),
    ):
        rc = main(["--jql", "project = NONE", "-o", "/tmp/x.json"])

    assert rc == 1
    assert "No issues matched" in capsys.readouterr().err
