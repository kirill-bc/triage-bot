"""Unit tests for the ``enrich`` subcommand and enrich_decision_file helper."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.mark.unit
def test_enrich_decision_file_writes_output(tmp_path: Path) -> None:
    from scripts.build_dashboard_seed import enrich_decision_file

    input_path = tmp_path / "in.json"
    output_path = tmp_path / "out.json"
    input_path.write_text(
        json.dumps([{"issue_key": "BC-1", "occurred_at": "2026-06-02T10:00:00Z"}]),
        encoding="utf-8",
    )
    enriched_rows = [
        {
            "issue_key": "BC-1",
            "triaged_at": "2026-06-02T10:00:00Z",
            "issue_created_at": "2026-05-01T08:00:00.000Z",
            "issue_name": "Broken checkout",
        },
    ]

    with patch(
        "scripts.build_dashboard_seed.enrich_rows_with_issue_created_at",
        return_value=enriched_rows,
    ) as enrich_mock:
        result = enrich_decision_file(
            input_path,
            output_path=output_path,
            env_file=None,
            skip_existing=True,
        )

    enrich_mock.assert_called_once()
    assert enrich_mock.call_args.kwargs["skip_existing"] is True
    assert result == enriched_rows
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload[0]["issue_created_at"] == "2026-05-01T08:00:00.000Z"


@pytest.mark.unit
def test_enrich_main_overwrites_input_by_default(tmp_path: Path) -> None:
    from scripts.build_dashboard_seed import main

    input_path = tmp_path / "decisions.json"
    input_path.write_text(
        json.dumps([{"issue_key": "BC-2", "occurred_at": "2026-06-02T11:00:00Z"}]),
        encoding="utf-8",
    )
    enriched_rows = [
        {
            "issue_key": "BC-2",
            "triaged_at": "2026-06-02T11:00:00Z",
            "issue_created_at": "2026-05-02T09:00:00.000Z",
        },
    ]

    with patch(
        "scripts.build_dashboard_seed.enrich_decision_file",
        return_value=enriched_rows,
    ) as enrich_file_mock:
        rc = main(["--env-file", str(tmp_path / "missing.env"), "enrich", str(input_path)])

    assert rc == 0
    enrich_file_mock.assert_called_once()
    assert enrich_file_mock.call_args.kwargs["output_path"] == input_path
    assert enrich_file_mock.call_args.kwargs["skip_existing"] is True


@pytest.mark.unit
def test_enrich_rows_skip_existing_avoids_refetch() -> None:
    from scripts.build_dashboard_seed import enrich_rows_with_issue_created_at

    rows = [
        {
            "issue_key": "BC-1",
            "issue_created_at": "2026-05-01T08:00:00.000Z",
            "issue_name": "Already complete",
            "triaged_at": "2026-06-02T10:00:00Z",
        },
        {"issue_key": "BC-2", "triaged_at": "2026-06-02T11:00:00Z"},
    ]

    from triage_service.adapters.jira_issue_fetcher import IssueDecisionMetadata

    class _FakeFetcher:
        def fetch_decision_metadata(self, issue_key: str, *, run_id: str) -> IssueDecisionMetadata:
            _ = run_id
            assert issue_key == "BC-2"
            return IssueDecisionMetadata(
                issue_created_at="2026-05-02T09:00:00.000Z",
                issue_name="Second issue",
            )

    with (
        patch("scripts.build_dashboard_seed.tqdm", side_effect=lambda it, **_: it),
        patch("triage_service.core.settings.load_settings", return_value=object()),
        patch(
            "triage_service.adapters.jira_issue_fetcher.JiraIssueFetcher",
            return_value=_FakeFetcher(),
        ),
        patch("scripts.build_dashboard_seed._ensure_triage_import_path"),
    ):
        enriched = enrich_rows_with_issue_created_at(rows, env_file=None, skip_existing=True)

    assert enriched[0]["issue_created_at"] == "2026-05-01T08:00:00.000Z"
    assert enriched[0]["triaged_at"] == "2026-06-02T10:00:00Z"
    assert "occurred_at" not in enriched[0]
    assert enriched[1]["issue_created_at"] == "2026-05-02T09:00:00.000Z"
    assert enriched[1]["issue_name"] == "Second issue"
    assert enriched[1]["triaged_at"] == "2026-06-02T11:00:00Z"
    assert "occurred_at" not in enriched[1]
