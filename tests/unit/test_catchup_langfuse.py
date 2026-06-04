"""Unit tests for catch-up helpers and the ``catchup`` subcommand."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.mark.unit
def test_load_decision_rows_from_json_missing_file_returns_empty(tmp_path: Path) -> None:
    from scripts.build_dashboard_seed import load_decision_rows_from_json

    assert load_decision_rows_from_json(tmp_path / "missing.json") == []


@pytest.mark.unit
def test_load_decision_rows_from_json_reads_array(tmp_path: Path) -> None:
    from scripts.build_dashboard_seed import load_decision_rows_from_json

    seed = tmp_path / "seed.json"
    seed.write_text(
        json.dumps([{"run_id": "a", "triaged_at": "2026-06-01T10:00:00Z"}]),
        encoding="utf-8",
    )
    rows = load_decision_rows_from_json(seed)
    assert rows[0]["run_id"] == "a"


@pytest.mark.unit
def test_load_decision_rows_from_json_rejects_non_array(tmp_path: Path) -> None:
    from scripts.build_dashboard_seed import load_decision_rows_from_json

    bad = tmp_path / "bad.json"
    bad.write_text('{"run_id": "a"}', encoding="utf-8")
    with pytest.raises(ValueError, match="JSON array"):
        load_decision_rows_from_json(bad)


@pytest.mark.unit
def test_latest_triaged_at_picks_max() -> None:
    from scripts.build_dashboard_seed import latest_triaged_at

    rows = [
        {"triaged_at": "2026-06-01T10:00:00Z"},
        {"triaged_at": "2026-06-02T15:39:50.720Z"},
        {"occurred_at": "2026-05-01T08:00:00Z"},
    ]
    latest = latest_triaged_at(rows)
    assert latest == datetime(2026, 6, 2, 15, 39, 50, 720000, tzinfo=timezone.utc)


@pytest.mark.unit
def test_collect_run_ids_skips_blank() -> None:
    from scripts.build_dashboard_seed import collect_run_ids

    assert collect_run_ids([{"run_id": "r1"}, {"run_id": "  "}, {}]) == {"r1"}


@pytest.mark.unit
def test_exclude_rows_with_run_ids() -> None:
    from scripts.build_dashboard_seed import exclude_rows_with_run_ids

    rows = [
        {"run_id": "keep", "issue_key": "BC-1"},
        {"run_id": "drop", "issue_key": "BC-2"},
    ]
    filtered = exclude_rows_with_run_ids(rows, known_run_ids={"drop"})
    assert [r["run_id"] for r in filtered] == ["keep"]


@pytest.mark.unit
def test_export_catchup_passes_seed_latest_as_min_occurred_at(tmp_path: Path) -> None:
    from scripts.build_dashboard_seed import export_catchup

    seed = tmp_path / "seed.json"
    seed.write_text(
        json.dumps([{"run_id": "old", "triaged_at": "2026-06-02T10:00:00Z"}]),
        encoding="utf-8",
    )
    exported = [
        {"run_id": "old", "triaged_at": "2026-06-02T10:00:00Z"},
        {"run_id": "new", "triaged_at": "2026-06-02T11:00:00Z"},
    ]

    with patch(
        "scripts.build_dashboard_seed.export_backfill",
        return_value=exported,
    ) as mock_export:
        rows = export_catchup(
            seed_path=seed,
            page_size=50,
            default_source="bug_created",
            trace_name="triage_issue_pipeline",
            project_filter="BC",
            enrich_jira_created=False,
        )

    assert mock_export.call_count == 1
    kwargs = mock_export.call_args.kwargs
    assert kwargs["min_occurred_at"] == datetime(2026, 6, 2, 10, 0, tzinfo=timezone.utc)
    assert kwargs["latest_only"] is False
    assert [r["run_id"] for r in rows] == ["new"]


@pytest.mark.unit
def test_export_catchup_rerun_is_idempotent(tmp_path: Path) -> None:
    from scripts.build_dashboard_seed import export_catchup

    seed = tmp_path / "seed.json"
    seed.write_text(
        json.dumps(
            [
                {"run_id": "r1", "triaged_at": "2026-06-02T10:00:00Z"},
                {"run_id": "r2", "triaged_at": "2026-06-02T11:00:00Z"},
            ],
        ),
        encoding="utf-8",
    )
    with patch(
        "scripts.build_dashboard_seed.export_backfill",
        return_value=[
            {"run_id": "r1", "triaged_at": "2026-06-02T10:00:00Z"},
            {"run_id": "r2", "triaged_at": "2026-06-02T11:00:00Z"},
        ],
    ):
        rows = export_catchup(
            seed_path=seed,
            page_size=50,
            default_source="bug_created",
            trace_name="triage_issue_pipeline",
            project_filter="BC",
            enrich_jira_created=False,
        )
    assert rows == []


@pytest.mark.unit
def test_catchup_main_writes_only_new_rows(tmp_path: Path) -> None:
    from scripts.build_dashboard_seed import main

    seed = tmp_path / "decisions.json"
    seed.write_text(
        json.dumps([{"run_id": "seed-run", "triaged_at": "2026-06-01T12:00:00Z"}]),
        encoding="utf-8",
    )
    output = tmp_path / "catchup.json"
    new_rows = [
        {
            "event_type": "triage_completed",
            "run_id": "new-run",
            "issue_key": "BC-9",
            "triaged_at": "2026-06-02T12:00:00Z",
        },
    ]

    with patch("scripts.build_dashboard_seed.export_catchup", return_value=new_rows):
        rc = main(
            [
                "--env-file",
                str(tmp_path / "missing.env"),
                "catchup",
                "--seed",
                str(seed),
                "--output",
                str(output),
                "--no-jira-created",
            ],
        )

    assert rc == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert len(payload) == 1
    assert payload[0]["run_id"] == "new-run"


@pytest.mark.unit
def test_catchup_main_passes_projects_filter(tmp_path: Path) -> None:
    from scripts.build_dashboard_seed import main

    seed = tmp_path / "decisions.json"
    seed.write_text("[]", encoding="utf-8")
    output = tmp_path / "catchup.json"

    with patch(
        "scripts.build_dashboard_seed.export_catchup", return_value=[]
    ) as mock_export:
        rc = main(
            [
                "--env-file",
                str(tmp_path / "missing.env"),
                "catchup",
                "--seed",
                str(seed),
                "--output",
                str(output),
                "--projects",
                "BC,TJC",
                "--no-jira-created",
            ],
        )

    assert rc == 0
    assert mock_export.call_args.kwargs["project_filter"] == "BC,TJC"
