"""Unit tests for the ``benchmark_summary`` post-hoc analysis module."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def _row(
    *,
    model: str = "openai/gpt-4o-mini",
    issue_key: str = "BC-1",
    bucket: str = "stable_bug",
    latency_ms: float = 1000.0,
    outcome_kind: str = "ok",
    outcome_category: str | None = None,
    outcome_message: str | None = None,
    success: bool = True,
    predicted_issue_type: str | None = "Bug",
    predicted_priority: str | None = "P2",
    ground_truth_issue_type: str = "Bug",
    ground_truth_priority: str | None = "P2",
    type_match: bool = True,
    priority_match: bool | None = True,
    notes: str | None = None,
) -> dict[str, object]:
    outcome: dict[str, object]
    if outcome_kind == "failure":
        outcome = {
            "kind": "failure",
            "category": outcome_category or "unknown",
            "message": outcome_message or "",
        }
    else:
        outcome = {
            "kind": "ok",
            "recommended_issue_type": predicted_issue_type,
            "recommended_priority": predicted_priority,
            "confidence": 0.9,
            "reason": "ok",
        }
    return {
        "model": model,
        "issue_key": issue_key,
        "bucket": bucket,
        "latency_ms": latency_ms,
        "outcome": outcome,
        "score": {
            "bucket": bucket,
            "issue_key": issue_key,
            "success": success,
            "predicted_issue_type": predicted_issue_type,
            "predicted_priority": predicted_priority,
            "ground_truth_issue_type": ground_truth_issue_type,
            "ground_truth_priority": ground_truth_priority,
            "type_match": type_match,
            "priority_match": priority_match,
            "notes": notes,
        },
    }


@pytest.mark.unit
def test_load_jsonl_rows_skips_blank_lines(tmp_path: Path) -> None:
    from scripts.benchmark.benchmark_summary import load_jsonl_rows

    path = tmp_path / "rows_x.jsonl"
    path.write_text(
        json.dumps(_row(issue_key="BC-1")) + "\n"
        + "\n"
        + json.dumps(_row(issue_key="BC-2")) + "\n",
        encoding="utf-8",
    )

    rows = load_jsonl_rows(path)

    assert [r["issue_key"] for r in rows] == ["BC-1", "BC-2"]


@pytest.mark.unit
def test_load_jsonl_rows_raises_for_malformed_line(tmp_path: Path) -> None:
    from scripts.benchmark.benchmark_summary import load_jsonl_rows

    path = tmp_path / "rows_bad.jsonl"
    path.write_text(json.dumps(_row()) + "\nnot-json\n", encoding="utf-8")

    with pytest.raises(ValueError, match="rows_bad.jsonl"):
        load_jsonl_rows(path)


@pytest.mark.unit
def test_compute_latency_stats_returns_zeros_for_empty() -> None:
    from scripts.benchmark.benchmark_summary import compute_latency_stats

    stats = compute_latency_stats([])

    assert stats.count == 0
    assert stats.total_ms == 0.0
    assert stats.mean_ms == 0.0
    assert stats.median_ms == 0.0
    assert stats.p95_ms == 0.0
    assert stats.min_ms == 0.0
    assert stats.max_ms == 0.0


@pytest.mark.unit
def test_compute_latency_stats_computes_basic_aggregates() -> None:
    from scripts.benchmark.benchmark_summary import compute_latency_stats

    stats = compute_latency_stats([100.0, 200.0, 300.0, 400.0, 500.0])

    assert stats.count == 5
    assert stats.total_ms == pytest.approx(1500.0)
    assert stats.mean_ms == pytest.approx(300.0)
    assert stats.median_ms == pytest.approx(300.0)
    assert stats.min_ms == pytest.approx(100.0)
    assert stats.max_ms == pytest.approx(500.0)
    assert 400.0 <= stats.p95_ms <= 500.0


@pytest.mark.unit
def test_compute_failure_counts_groups_by_category() -> None:
    from scripts.benchmark.benchmark_summary import compute_failure_counts

    records = [
        _row(outcome_kind="ok"),
        _row(outcome_kind="failure", outcome_category="invalid_model_output"),
        _row(outcome_kind="failure", outcome_category="invalid_model_output"),
        _row(outcome_kind="failure", outcome_category="jira_fetch_failed"),
    ]

    counts = compute_failure_counts(records)

    assert counts.total == 3
    assert dict(counts.by_category) == {
        "invalid_model_output": 2,
        "jira_fetch_failed": 1,
    }


@pytest.mark.unit
def test_summarize_model_run_aggregates_per_bucket_and_overall(tmp_path: Path) -> None:
    from scripts.benchmark.benchmark_summary import summarize_model_run

    records = [
        _row(bucket="stable_bug", success=True),
        _row(bucket="stable_bug", success=False, type_match=True, priority_match=False),
        _row(
            bucket="story_from_bug",
            success=True,
            predicted_issue_type="Story",
            predicted_priority=None,
            ground_truth_issue_type="Story",
            ground_truth_priority=None,
            type_match=True,
            priority_match=None,
        ),
        _row(
            bucket="story_from_bug",
            success=False,
            predicted_issue_type="Bug",
            ground_truth_issue_type="Story",
            ground_truth_priority=None,
            type_match=False,
            priority_match=False,
        ),
    ]

    summary = summarize_model_run(records=records, source_file=tmp_path / "rows_x.jsonl")

    assert summary.row_count == 4
    assert summary.overall.total == 4
    assert summary.overall.successes == 2
    assert {b.bucket: (b.total, b.successes) for b in summary.buckets} == {
        "stable_bug": (2, 1),
        "story_from_bug": (2, 1),
    }


@pytest.mark.unit
def test_summarize_model_run_marks_failed_predictions_in_confusion(tmp_path: Path) -> None:
    from scripts.benchmark.benchmark_summary import summarize_model_run

    records = [
        _row(
            bucket="stable_bug",
            outcome_kind="failure",
            outcome_category="invalid_model_output",
            success=False,
            predicted_issue_type=None,
            predicted_priority=None,
            type_match=False,
            priority_match=False,
        ),
        _row(
            bucket="stable_bug",
            success=True,
            predicted_issue_type="Bug",
            ground_truth_issue_type="Bug",
        ),
    ]

    summary = summarize_model_run(records=records, source_file=tmp_path / "rows_x.jsonl")

    assert summary.confusion_issue_type[("Bug", "_failed")] == 1
    assert summary.confusion_issue_type[("Bug", "Bug")] == 1


@pytest.mark.unit
def test_summarize_model_run_derives_model_from_first_record(tmp_path: Path) -> None:
    from scripts.benchmark.benchmark_summary import summarize_model_run

    records = [_row(model="x-ai/grok-4.3", bucket="stable_bug")]

    summary = summarize_model_run(records=records, source_file=tmp_path / "rows.jsonl")

    assert summary.model == "x-ai/grok-4.3"


@pytest.mark.unit
def test_summarize_model_run_falls_back_to_filename_when_no_records(tmp_path: Path) -> None:
    from scripts.benchmark.benchmark_summary import summarize_model_run

    summary = summarize_model_run(
        records=[],
        source_file=tmp_path / "rows_openai_gpt-4o-mini.jsonl",
    )

    assert summary.row_count == 0
    assert summary.model == "openai/gpt-4o-mini"
    assert summary.overall.total == 0


@pytest.mark.unit
def test_discover_row_files_returns_sorted_rows_jsonl(tmp_path: Path) -> None:
    from scripts.benchmark.benchmark_summary import discover_row_files

    (tmp_path / "rows_b.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "rows_a.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "summary.json").write_text("{}", encoding="utf-8")
    (tmp_path / "fetched_issues_cache.json").write_text("{}", encoding="utf-8")
    (tmp_path / "rows_other.txt").write_text("ignored", encoding="utf-8")

    files = discover_row_files(tmp_path)

    assert [p.name for p in files] == ["rows_a.jsonl", "rows_b.jsonl"]


@pytest.mark.unit
def test_format_summary_text_shows_model_buckets_and_overall(tmp_path: Path) -> None:
    from scripts.benchmark.benchmark_summary import (
        format_summary_text,
        summarize_model_run,
    )

    records = [
        _row(model="openai/gpt-4o-mini", bucket="stable_bug", success=True),
        _row(model="openai/gpt-4o-mini", bucket="stable_bug", success=False),
    ]

    summary = summarize_model_run(records=records, source_file=tmp_path / "rows_x.jsonl")
    text = format_summary_text(summary)

    assert "openai/gpt-4o-mini" in text
    assert "stable_bug" in text
    assert "OVERALL" in text


@pytest.mark.unit
def test_summary_to_dict_mirrors_run_summary_keys(tmp_path: Path) -> None:
    from scripts.benchmark.benchmark_summary import summarize_model_run, summary_to_dict

    records = [_row(bucket="stable_bug", success=True)]
    summary = summarize_model_run(records=records, source_file=tmp_path / "rows_x.jsonl")

    payload = summary_to_dict(summary)

    assert payload["model"] == "openai/gpt-4o-mini"
    assert payload["row_count"] == 1
    assert payload["overall"]["total"] == 1
    assert payload["overall"]["successes"] == 1
    assert payload["buckets"][0]["bucket"] == "stable_bug"
    assert "Bug->Bug" in payload["confusion_issue_type"]
    assert "latency" in payload
    assert "failures" in payload
