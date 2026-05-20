"""Unit tests for classification benchmark CSV loading and bucket scoring."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from triage_service.adapters.jira_issue_fetcher import (
    AttachmentRef,
    FetchedIssue,
    JiraIssueFetcher,
)
from triage_service.core.triage_recommendation_parser import TriageRecommendation


@pytest.mark.unit
def test_load_benchmark_csv_parses_header_and_rows(tmp_path: Path) -> None:
    from scripts.benchmark.classification_benchmark import BenchmarkCsvRow, load_benchmark_csv

    csv_path = tmp_path / "bench.csv"
    csv_path.write_text(
        "benchmark_bucket,issue_key,priority_change_from,priority_change_to,"
        "issue_type_change_from,issue_type_change_to\n"
        "stable_bug,BC-1,,,,\n"
        "story_from_bug,BC-2,,,Bug,Story\n",
        encoding="utf-8",
    )
    rows = load_benchmark_csv(csv_path)
    assert rows == [
        BenchmarkCsvRow(
            benchmark_bucket="stable_bug",
            issue_key="BC-1",
            priority_change_from="",
            priority_change_to="",
            issue_type_change_from="",
            issue_type_change_to="",
        ),
        BenchmarkCsvRow(
            benchmark_bucket="story_from_bug",
            issue_key="BC-2",
            priority_change_from="",
            priority_change_to="",
            issue_type_change_from="Bug",
            issue_type_change_to="Story",
        ),
    ]


@pytest.mark.unit
def test_score_stable_bug_success_when_bug_and_priority_matches_jira() -> None:
    from scripts.benchmark.classification_benchmark import (
        BenchmarkCsvRow,
        score_benchmark_prediction,
    )

    row = BenchmarkCsvRow(
        benchmark_bucket="stable_bug",
        issue_key="BC-9",
        priority_change_from="",
        priority_change_to="",
        issue_type_change_from="",
        issue_type_change_to="",
    )
    issue = FetchedIssue(
        issue_key="BC-9",
        summary="x",
        description=None,
        issue_type="Bug",
        priority="P2",
        reporter="r",
    )
    rec = TriageRecommendation(
        recommended_issue_type="Bug",
        recommended_priority="P2",
        confidence=0.9,
        reason="ok",
    )
    s = score_benchmark_prediction(row, issue, rec)
    assert s.success is True
    assert s.type_match is True
    assert s.priority_match is True


@pytest.mark.unit
def test_score_stable_bug_fails_when_priority_differs_from_jira() -> None:
    from scripts.benchmark.classification_benchmark import (
        BenchmarkCsvRow,
        score_benchmark_prediction,
    )

    row = BenchmarkCsvRow(
        benchmark_bucket="stable_bug",
        issue_key="BC-9",
        priority_change_from="",
        priority_change_to="",
        issue_type_change_from="",
        issue_type_change_to="",
    )
    issue = FetchedIssue(
        issue_key="BC-9",
        summary="x",
        description=None,
        issue_type="Bug",
        priority="P2",
        reporter="r",
    )
    rec = TriageRecommendation(
        recommended_issue_type="Bug",
        recommended_priority="P3",
        confidence=0.9,
        reason="ok",
    )
    s = score_benchmark_prediction(row, issue, rec)
    assert s.success is False
    assert s.type_match is True
    assert s.priority_match is False


@pytest.mark.unit
def test_score_story_from_bug_success_when_story() -> None:
    from scripts.benchmark.classification_benchmark import (
        BenchmarkCsvRow,
        score_benchmark_prediction,
    )

    row = BenchmarkCsvRow(
        benchmark_bucket="story_from_bug",
        issue_key="BC-9",
        priority_change_from="",
        priority_change_to="",
        issue_type_change_from="Bug",
        issue_type_change_to="Story",
    )
    issue = FetchedIssue(
        issue_key="BC-9",
        summary="x",
        description=None,
        issue_type="Story",
        priority="P3",
        reporter="r",
    )
    rec = TriageRecommendation(
        recommended_issue_type="Story",
        recommended_priority=None,
        confidence=0.8,
        reason="narrative",
    )
    s = score_benchmark_prediction(row, issue, rec)
    assert s.success is True
    assert s.type_match is True
    assert s.priority_match is None


@pytest.mark.unit
def test_score_story_from_bug_fails_when_bug() -> None:
    from scripts.benchmark.classification_benchmark import (
        BenchmarkCsvRow,
        score_benchmark_prediction,
    )

    row = BenchmarkCsvRow(
        benchmark_bucket="story_from_bug",
        issue_key="BC-9",
        priority_change_from="",
        priority_change_to="",
        issue_type_change_from="Bug",
        issue_type_change_to="Story",
    )
    issue = FetchedIssue(
        issue_key="BC-9",
        summary="x",
        description=None,
        issue_type="Bug",
        priority="P1",
        reporter="r",
    )
    rec = TriageRecommendation(
        recommended_issue_type="Bug",
        recommended_priority="P1",
        confidence=0.8,
        reason="defect",
    )
    s = score_benchmark_prediction(row, issue, rec)
    assert s.success is False
    assert s.type_match is False
    assert s.priority_match is False


@pytest.mark.unit
def test_score_misprioritized_bug_success_when_bug_and_priority_matches_human_fix() -> None:
    from scripts.benchmark.classification_benchmark import (
        BenchmarkCsvRow,
        score_benchmark_prediction,
    )

    row = BenchmarkCsvRow(
        benchmark_bucket="misprioritized_bug",
        issue_key="BC-9",
        priority_change_from="P3",
        priority_change_to="P0",
        issue_type_change_from="",
        issue_type_change_to="",
    )
    issue = FetchedIssue(
        issue_key="BC-9",
        summary="x",
        description=None,
        issue_type="Bug",
        priority="P0",
        reporter="r",
    )
    rec = TriageRecommendation(
        recommended_issue_type="Bug",
        recommended_priority="P0",
        confidence=0.9,
        reason="severe",
    )
    s = score_benchmark_prediction(row, issue, rec)
    assert s.success is True
    assert s.type_match is True
    assert s.priority_match is True


@pytest.mark.unit
def test_score_misprioritized_bug_fails_when_model_echoes_old_wrong_priority() -> None:
    from scripts.benchmark.classification_benchmark import (
        BenchmarkCsvRow,
        score_benchmark_prediction,
    )

    row = BenchmarkCsvRow(
        benchmark_bucket="misprioritized_bug",
        issue_key="BC-9",
        priority_change_from="P3",
        priority_change_to="P0",
        issue_type_change_from="",
        issue_type_change_to="",
    )
    issue = FetchedIssue(
        issue_key="BC-9",
        summary="x",
        description=None,
        issue_type="Bug",
        priority="P0",
        reporter="r",
    )
    rec = TriageRecommendation(
        recommended_issue_type="Bug",
        recommended_priority="P3",
        confidence=0.9,
        reason="stale",
    )
    s = score_benchmark_prediction(row, issue, rec)
    assert s.success is False
    assert s.type_match is True
    assert s.priority_match is False


@pytest.mark.unit
def test_aggregate_bucket_accuracy_counts_successes() -> None:
    from scripts.benchmark.classification_benchmark import (
        BenchmarkCsvRow,
        aggregate_bucket_summaries,
        score_benchmark_prediction,
    )
    from triage_service.core.triage_recommendation_parser import TriageRecommendation

    row_stable = BenchmarkCsvRow(
        benchmark_bucket="stable_bug",
        issue_key="BC-1",
        priority_change_from="",
        priority_change_to="",
        issue_type_change_from="",
        issue_type_change_to="",
    )
    issue = FetchedIssue(
        issue_key="BC-1",
        summary="s",
        description=None,
        issue_type="Bug",
        priority="P1",
        reporter="r",
    )
    ok = score_benchmark_prediction(
        row_stable,
        issue,
        TriageRecommendation(
            recommended_issue_type="Bug",
            recommended_priority="P1",
            confidence=1.0,
            reason="x",
        ),
    )
    bad = score_benchmark_prediction(
        row_stable,
        issue,
        TriageRecommendation(
            recommended_issue_type="Bug",
            recommended_priority="P2",
            confidence=1.0,
            reason="x",
        ),
    )
    summaries = aggregate_bucket_summaries([ok, bad])
    stable = next(s for s in summaries if s.bucket == "stable_bug")
    assert stable.total == 2
    assert stable.successes == 1
    assert stable.accuracy == pytest.approx(0.5)


@pytest.mark.unit
def test_score_benchmark_skipped_records_failure() -> None:
    from scripts.benchmark.classification_benchmark import (
        BenchmarkCsvRow,
        score_benchmark_skipped,
    )

    row = BenchmarkCsvRow(
        benchmark_bucket="stable_bug",
        issue_key="BC-1",
        priority_change_from="",
        priority_change_to="",
        issue_type_change_from="",
        issue_type_change_to="",
    )
    s = score_benchmark_skipped(row, reason="fetch failed")
    assert s.success is False
    assert s.notes == "fetch failed"


@pytest.mark.unit
def test_aggregate_overall_accuracy() -> None:
    from scripts.benchmark.classification_benchmark import (
        BenchmarkCsvRow,
        aggregate_overall,
        score_benchmark_prediction,
    )
    from triage_service.core.triage_recommendation_parser import TriageRecommendation

    row = BenchmarkCsvRow(
        benchmark_bucket="story_from_bug",
        issue_key="BC-1",
        priority_change_from="",
        priority_change_to="",
        issue_type_change_from="Bug",
        issue_type_change_to="Story",
    )
    issue = FetchedIssue(
        issue_key="BC-1",
        summary="s",
        description=None,
        issue_type="Story",
        priority=None,
        reporter="r",
    )
    scores = [
        score_benchmark_prediction(
            row,
            issue,
            TriageRecommendation(
                recommended_issue_type="Story",
                recommended_priority=None,
                confidence=1.0,
                reason="x",
            ),
        ),
    ]
    overall = aggregate_overall(scores)
    assert overall.total == 1
    assert overall.successes == 1
    assert overall.accuracy == 1.0


@pytest.mark.unit
def test_ordered_unique_issue_keys_preserves_order_and_dedups() -> None:
    from scripts.benchmark.classification_benchmark import (
        BenchmarkCsvRow,
        ordered_unique_issue_keys,
    )

    rows = [
        BenchmarkCsvRow("stable_bug", "BC-2", "", "", "", ""),
        BenchmarkCsvRow("stable_bug", "BC-1", "", "", "", ""),
        BenchmarkCsvRow("stable_bug", "BC-2", "", "", "", ""),
    ]
    assert ordered_unique_issue_keys(rows) == ["BC-2", "BC-1"]


@pytest.mark.unit
def test_load_benchmark_issue_fetch_cache_missing_returns_none(tmp_path: Path) -> None:
    from scripts.benchmark.classification_benchmark import load_benchmark_issue_fetch_cache

    assert load_benchmark_issue_fetch_cache(tmp_path / "missing.json") is None


@pytest.mark.unit
def test_benchmark_issue_fetch_cache_roundtrip(tmp_path: Path) -> None:
    from scripts.benchmark.classification_benchmark import (
        load_benchmark_issue_fetch_cache,
        write_benchmark_issue_fetch_cache,
    )

    issue = FetchedIssue(
        issue_key="BC-9",
        summary="s",
        description=None,
        issue_type="Bug",
        priority="P2",
        reporter="r",
    )
    path = tmp_path / "cache.json"
    write_benchmark_issue_fetch_cache(
        path,
        issues={"BC-9": issue},
        failures={"BC-8": "nope"},
        fetched_at="2026-01-01T00:00:00Z",
    )
    loaded = load_benchmark_issue_fetch_cache(path)
    assert loaded is not None
    issues, failures, when = loaded
    assert when == "2026-01-01T00:00:00Z"
    assert issues["BC-9"] == issue
    assert failures["BC-8"] == "nope"


@pytest.mark.unit
def test_issue_has_inline_images_true_for_inline_image_attachment() -> None:
    from triage_service.adapters.image_context_extractor import issue_has_inline_images

    issue = FetchedIssue(
        issue_key="BC-1",
        summary="s",
        description="see screenshot",
        issue_type="Bug",
        priority="P2",
        reporter="r",
        attachments=[
            AttachmentRef(
                id="1",
                filename="shot.png",
                mime_type="image/png",
                inline=True,
            ),
        ],
    )
    assert issue_has_inline_images(issue) is True


@pytest.mark.unit
def test_issue_has_inline_images_false_without_inline_images() -> None:
    from triage_service.adapters.image_context_extractor import issue_has_inline_images

    issue = FetchedIssue(
        issue_key="BC-1",
        summary="s",
        description=None,
        issue_type="Bug",
        priority="P2",
        reporter="r",
        attachments=[
            AttachmentRef(
                id="1",
                filename="log.txt",
                mime_type="text/plain",
                inline=True,
            ),
            AttachmentRef(
                id="2",
                filename="shot.png",
                mime_type="image/png",
                inline=False,
            ),
        ],
    )
    assert issue_has_inline_images(issue) is False


@pytest.mark.unit
def test_aggregate_image_stratum_summaries_splits_has_images_vs_text_only() -> None:
    from scripts.benchmark.classification_benchmark import (
        BenchmarkCsvRow,
        aggregate_image_stratum_summaries,
        score_benchmark_prediction,
    )

    row = BenchmarkCsvRow(
        benchmark_bucket="stable_bug",
        issue_key="BC-1",
        priority_change_from="",
        priority_change_to="",
        issue_type_change_from="",
        issue_type_change_to="",
    )
    issue = FetchedIssue(
        issue_key="BC-1",
        summary="s",
        description=None,
        issue_type="Bug",
        priority="P1",
        reporter="r",
    )
    ok = score_benchmark_prediction(
        row,
        issue,
        TriageRecommendation(
            recommended_issue_type="Bug",
            recommended_priority="P1",
            confidence=1.0,
            reason="x",
        ),
    )
    bad = score_benchmark_prediction(
        row,
        issue,
        TriageRecommendation(
            recommended_issue_type="Bug",
            recommended_priority="P2",
            confidence=1.0,
            reason="x",
        ),
    )
    summaries = aggregate_image_stratum_summaries(
        [
            (ok, True),
            (bad, True),
            (ok, False),
        ],
    )
    by_stratum = {s.stratum: s for s in summaries}
    assert by_stratum["has_images"].total == 2
    assert by_stratum["has_images"].successes == 1
    assert by_stratum["has_images"].accuracy == pytest.approx(0.5)
    assert by_stratum["text_only"].total == 1
    assert by_stratum["text_only"].successes == 1
    assert by_stratum["text_only"].accuracy == pytest.approx(1.0)


@pytest.mark.unit
def test_resolve_benchmark_image_context_enabled_respects_cli_override() -> None:
    from scripts.benchmark.classification_benchmark import (
        resolve_benchmark_image_context_enabled,
    )

    assert (
        resolve_benchmark_image_context_enabled(cli_enable=True, settings_enabled=False)
        is True
    )
    assert (
        resolve_benchmark_image_context_enabled(cli_enable=False, settings_enabled=True)
        is False
    )
    assert (
        resolve_benchmark_image_context_enabled(cli_enable=None, settings_enabled=True)
        is True
    )


@pytest.mark.unit
def test_merge_cached_issues_with_fetch_only_hits_network_for_missing_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts.benchmark.classification_benchmark import merge_cached_issues_with_fetch

    monkeypatch.setenv("JIRA_API_KEY", "jira-api-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-token")
    monkeypatch.setenv("TRIAGE_WEBHOOK_TOKEN", "triage-token")
    monkeypatch.setenv("JIRA_CLOUD_ID", "cloud-id-test")
    monkeypatch.setenv("JIRA_USER_EMAIL", "bot@example.com")
    from triage_service.core.settings import AppSettings

    settings = AppSettings()
    cached = FetchedIssue(
        issue_key="BC-1",
        summary="one",
        description=None,
        issue_type="Bug",
        priority="P1",
        reporter="r",
    )

    def jira_handler(request: httpx.Request) -> httpx.Response:
        assert "BC-2" in str(request.url)
        assert "BC-1" not in str(request.url)
        payload = {
            "key": "BC-2",
            "fields": {
                "summary": "two",
                "description": None,
                "issuetype": {"name": "Story"},
                "priority": None,
                "reporter": {"displayName": "r2"},
            },
        }
        return httpx.Response(200, json=payload)

    transport = httpx.MockTransport(jira_handler)
    with httpx.Client(transport=transport) as client:
        fetcher = JiraIssueFetcher(settings, client=client)
        issues, failures = merge_cached_issues_with_fetch(
            ["BC-1", "BC-2"],
            fetcher,
            cached_issues={"BC-1": cached},
            cached_failures={},
        )
    assert issues["BC-1"] == cached
    assert issues["BC-2"].issue_key == "BC-2"
    assert issues["BC-2"].summary == "two"
    assert failures == {}
