"""Bulk triage CLI."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest

from triage_service.adapters.jira_jql_search import JiraSearchIssueRef
from triage_service.core.settings import AppSettings
from triage_service.core.triage_handler import TriageRunner
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


@pytest.mark.unit
def test_run_bulk_triage_forwards_auto_apply_flags_to_run_cli_triage() -> None:
    from triage_bulk_cli import run_bulk_triage

    refs = [JiraSearchIssueRef(issue_key="TJC-3", issue_type="Bug", priority="P2")]
    forwarded: list[tuple[bool | None, bool | None, bool | None]] = []

    class _Settings:
        triage_image_context_enabled = False

    def _fake_run_cli_triage(
        issue_key: str,
        *,
        project: str | None = None,
        runner: object | None = None,
        post_mismatch_comments: bool = True,
        apply_to_jira: bool = True,
        auto_apply_deescalation: bool | None = None,
        auto_apply_escalation: bool | None = None,
        auto_apply_bug_to_story: bool | None = None,
    ) -> TriageSyncResult:
        _ = (issue_key, project, runner, post_mismatch_comments, apply_to_jira)
        forwarded.append(
            (auto_apply_deescalation, auto_apply_escalation, auto_apply_bug_to_story),
        )
        return TriageSyncResult(
            outcome=TriageRecommendation(
                recommended_issue_type="Story",
                recommended_priority=None,
                confidence=0.9,
                reason="Product work.",
            ),
        )

    with patch("triage_bulk_cli.run_cli_triage", side_effect=_fake_run_cli_triage):
        _ = run_bulk_triage(
            refs,
            settings=cast(AppSettings, _Settings()),
            runner=cast(TriageRunner, object()),
            auto_apply_deescalation=True,
            auto_apply_bug_to_story=False,
            show_progress=False,
        )

    assert forwarded == [(True, None, False)]


class _ConcurrencyTrackingRunner:
    """Shared stub runner recording the high-water mark of overlapping run_sync calls."""

    def __init__(self, hold_seconds: float) -> None:
        self._hold_seconds = hold_seconds
        self._lock = threading.Lock()
        self._current = 0
        self.peak = 0

    def run_sync(
        self,
        issue_key: str,
        project: str,
        source: str,
        *,
        run_id: str,
    ) -> TriageSyncResult:
        _ = (project, source, run_id)
        with self._lock:
            self._current += 1
            self.peak = max(self.peak, self._current)
        time.sleep(self._hold_seconds)
        with self._lock:
            self._current -= 1
        return TriageSyncResult(
            outcome=TriageRecommendation(
                recommended_issue_type="Story",
                recommended_priority=None,
                confidence=0.5,
                reason=f"stub for {issue_key}",
            ),
        )

    def flush_inference_telemetry(self) -> None:
        return None


@pytest.mark.unit
def test_run_bulk_triage_respects_concurrency_cap() -> None:
    from triage_bulk_cli import run_bulk_triage

    refs = [
        JiraSearchIssueRef(issue_key=f"TJC-{i}", issue_type="Bug", priority="P3")
        for i in range(6)
    ]
    runner = _ConcurrencyTrackingRunner(hold_seconds=0.2)

    class _Settings:
        triage_image_context_enabled = False

    rows = run_bulk_triage(
        refs,
        settings=cast(AppSettings, _Settings()),
        runner=runner,
        concurrency=2,
        show_progress=False,
    )
    assert len(rows) == 6
    assert runner.peak == 2


@pytest.mark.unit
def test_run_bulk_triage_preserves_input_order_when_completions_are_out_of_order() -> None:
    from triage_bulk_cli import run_bulk_triage

    refs = [
        JiraSearchIssueRef(issue_key="TJC-1", issue_type="Bug", priority="P3"),
        JiraSearchIssueRef(issue_key="TJC-2", issue_type="Bug", priority="P2"),
        JiraSearchIssueRef(issue_key="TJC-3", issue_type="Bug", priority="P1"),
    ]
    hold_seconds_by_key = {"TJC-1": 0.3, "TJC-2": 0.1, "TJC-3": 0.2}

    class _VariableDelayRunner:
        def run_sync(
            self,
            issue_key: str,
            project: str,
            source: str,
            *,
            run_id: str,
        ) -> TriageSyncResult:
            _ = (project, source, run_id)
            time.sleep(hold_seconds_by_key[issue_key])
            return TriageSyncResult(
                outcome=TriageRecommendation(
                    recommended_issue_type="Story",
                    recommended_priority=None,
                    confidence=0.5,
                    reason=issue_key,
                ),
            )

        def flush_inference_telemetry(self) -> None:
            return None

    class _Settings:
        triage_image_context_enabled = False

    rows = run_bulk_triage(
        refs,
        settings=cast(AppSettings, _Settings()),
        runner=_VariableDelayRunner(),
        concurrency=3,
        show_progress=False,
    )
    assert [row.issue_key for row in rows] == ["TJC-1", "TJC-2", "TJC-3"]


@pytest.mark.unit
def test_run_bulk_triage_isolates_one_failure_under_concurrency() -> None:
    from triage_bulk_cli import run_bulk_triage

    refs = [
        JiraSearchIssueRef(issue_key="TJC-1", issue_type="Bug", priority="P3"),
        JiraSearchIssueRef(issue_key="TJC-2", issue_type="Bug", priority="P2"),
        JiraSearchIssueRef(issue_key="TJC-3", issue_type="Bug", priority="P1"),
    ]

    class _MixedRunner:
        def run_sync(
            self,
            issue_key: str,
            project: str,
            source: str,
            *,
            run_id: str,
        ) -> TriageSyncResult:
            _ = (project, source, run_id)
            if issue_key == "TJC-2":
                return TriageSyncResult(outcome=fallback_for_exception(RuntimeError("boom")))
            return TriageSyncResult(
                outcome=TriageRecommendation(
                    recommended_issue_type="Story",
                    recommended_priority=None,
                    confidence=0.5,
                    reason=issue_key,
                ),
            )

        def flush_inference_telemetry(self) -> None:
            return None

    class _Settings:
        triage_image_context_enabled = False

    rows = run_bulk_triage(
        refs,
        settings=cast(AppSettings, _Settings()),
        runner=_MixedRunner(),
        concurrency=3,
        show_progress=False,
    )
    statuses = {row.issue_key: row.status for row in rows}
    assert statuses == {"TJC-1": "completed", "TJC-2": "failed", "TJC-3": "completed"}


@pytest.mark.unit
def test_run_bulk_triage_builds_one_runner_per_worker_when_none_injected() -> None:
    from triage_bulk_cli import run_bulk_triage

    refs = [
        JiraSearchIssueRef(issue_key=f"TJC-{i}", issue_type="Bug", priority="P3")
        for i in range(4)
    ]
    build_calls: list[int] = []
    build_lock = threading.Lock()

    class _WorkerRunner:
        def run_sync(
            self,
            issue_key: str,
            project: str,
            source: str,
            *,
            run_id: str,
        ) -> TriageSyncResult:
            _ = (issue_key, project, source, run_id)
            time.sleep(0.1)
            return TriageSyncResult(
                outcome=TriageRecommendation(
                    recommended_issue_type="Story",
                    recommended_priority=None,
                    confidence=0.5,
                    reason="worker stub",
                ),
            )

        def flush_inference_telemetry(self) -> None:
            return None

    def _build_handler(**kwargs: object) -> _WorkerRunner:
        _ = kwargs
        with build_lock:
            build_calls.append(1)
        return _WorkerRunner()

    class _Settings:
        triage_image_context_enabled = False

    with patch("triage_bulk_cli.build_default_triage_handler", side_effect=_build_handler):
        rows = run_bulk_triage(
            refs,
            settings=cast(AppSettings, _Settings()),
            concurrency=4,
            show_progress=False,
        )

    assert len(rows) == 4
    assert 1 < len(build_calls) <= 4


@pytest.mark.unit
def test_main_forwards_concurrency_flag_to_run_bulk_triage(tmp_path: Path) -> None:
    from triage_bulk_cli import main

    refs = [JiraSearchIssueRef(issue_key="TJC-7", issue_type="Bug", priority="P2")]
    captured: dict[str, object] = {}

    def _fake_run_bulk_triage(
        refs_arg: list[JiraSearchIssueRef],
        **kwargs: object,
    ) -> list[object]:
        _ = refs_arg
        captured["concurrency"] = kwargs.get("concurrency")
        return []

    class _Settings:
        triage_image_context_enabled = False

    out_file = tmp_path / "report.json"
    with (
        patch("triage_service.core.settings.load_settings", return_value=_Settings()),
        patch("triage_bulk_cli.search_issues_by_jql", return_value=refs),
        patch("triage_bulk_cli.run_bulk_triage", side_effect=_fake_run_bulk_triage),
    ):
        main(
            [
                "--jql",
                "project = TJC",
                "-o",
                str(out_file),
                "--concurrency",
                "8",
            ],
        )

    assert captured["concurrency"] == 8


@pytest.mark.unit
def test_main_returns_2_when_concurrency_less_than_one(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from triage_bulk_cli import main

    rc = main(["--jql", "project = TJC", "-o", "/tmp/x.json", "--concurrency", "0"])
    assert rc == 2
    assert "--concurrency" in capsys.readouterr().err
