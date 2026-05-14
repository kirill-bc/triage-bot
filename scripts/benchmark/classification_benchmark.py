"""Curated-issue benchmark: CSV rows, bucket-aware scoring, and aggregate metrics."""

from __future__ import annotations

import csv
import json
import uuid
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from triage_service.adapters.jira_issue_fetcher import (
    FetchedIssue,
    JiraIssueFetchError,
    JiraIssueFetcher,
)
from triage_service.core.triage_fallback import TriageFailure
from triage_service.core.triage_recommendation_parser import IssueTypeLiteral, TriageRecommendation

CSV_COLUMNS = (
    "benchmark_bucket",
    "issue_key",
    "priority_change_from",
    "priority_change_to",
    "issue_type_change_from",
    "issue_type_change_to",
)

KNOWN_BUCKETS = frozenset({"stable_bug", "story_from_bug", "misprioritized_bug"})
_BUG_PRIOS = frozenset({"P0", "P1", "P2", "P3", "P4"})


@dataclass(frozen=True)
class BenchmarkCsvRow:
    """One labeled issue from ``data/issue_benchmark_dataset.csv``."""

    benchmark_bucket: str
    issue_key: str
    priority_change_from: str
    priority_change_to: str
    issue_type_change_from: str
    issue_type_change_to: str


@dataclass(frozen=True)
class BenchmarkRowScore:
    """Per-issue score after triage vs human bucket ground truth."""

    bucket: str
    issue_key: str
    success: bool
    predicted_issue_type: str | None
    predicted_priority: str | None
    ground_truth_issue_type: IssueTypeLiteral
    ground_truth_priority: str | None
    type_match: bool
    priority_match: bool | None
    notes: str | None = None


@dataclass(frozen=True)
class BenchmarkBucketSummary:
    """Aggregate success rate for one ``benchmark_bucket`` value."""

    bucket: str
    total: int
    successes: int

    @property
    def accuracy(self) -> float:
        if self.total <= 0:
            return 0.0
        return self.successes / self.total


@dataclass(frozen=True)
class BenchmarkOverallSummary:
    """Dataset-wide success rate (same definition as per-row ``success``)."""

    total: int
    successes: int

    @property
    def accuracy(self) -> float:
        if self.total <= 0:
            return 0.0
        return self.successes / self.total


def load_benchmark_csv(path: Path) -> list[BenchmarkCsvRow]:
    """Load benchmark rows; raises if the header does not match ``CSV_COLUMNS``."""
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            msg = "CSV has no header row"
            raise ValueError(msg)
        header = tuple(reader.fieldnames)
        if header != CSV_COLUMNS:
            msg = f"Unexpected CSV header {header!r}; expected {CSV_COLUMNS!r}"
            raise ValueError(msg)
        rows: list[BenchmarkCsvRow] = []
        for raw in reader:
            bucket = (raw.get("benchmark_bucket") or "").strip()
            if bucket and bucket not in KNOWN_BUCKETS:
                msg = f"Unknown benchmark_bucket {bucket!r} in {path}"
                raise ValueError(msg)
            rows.append(
                BenchmarkCsvRow(
                    benchmark_bucket=bucket,
                    issue_key=(raw.get("issue_key") or "").strip(),
                    priority_change_from=(raw.get("priority_change_from") or "").strip(),
                    priority_change_to=(raw.get("priority_change_to") or "").strip(),
                    issue_type_change_from=(raw.get("issue_type_change_from") or "").strip(),
                    issue_type_change_to=(raw.get("issue_type_change_to") or "").strip(),
                ),
            )
    return rows


def ground_truth_issue_type(row: BenchmarkCsvRow) -> IssueTypeLiteral:
    """Human issue-type label for metrics (from bucket and optional CSV columns)."""
    if row.benchmark_bucket == "story_from_bug":
        to = row.issue_type_change_to.strip()
        if to == "Bug":
            return "Bug"
        if to == "Story":
            return "Story"
        return "Story"
    if row.benchmark_bucket in ("stable_bug", "misprioritized_bug"):
        return "Bug"
    msg = f"Unknown benchmark_bucket: {row.benchmark_bucket!r}"
    raise ValueError(msg)


def _ground_truth_priority_bug_bucket(row: BenchmarkCsvRow, issue: FetchedIssue) -> str | None:
    if row.benchmark_bucket == "stable_bug":
        p = issue.priority.strip() if issue.priority else ""
        return p if p in _BUG_PRIOS else None
    if row.benchmark_bucket == "misprioritized_bug":
        p = row.priority_change_to.strip()
        return p if p in _BUG_PRIOS else None
    return None


def score_benchmark_prediction(
    row: BenchmarkCsvRow,
    issue: FetchedIssue,
    rec: TriageRecommendation,
) -> BenchmarkRowScore:
    """Score a successful parse path (strict recommendation) against the row's bucket rules."""
    gt_type = ground_truth_issue_type(row)
    pred_type = rec.recommended_issue_type
    pred_pri = rec.recommended_priority
    type_match = pred_type == gt_type

    if row.benchmark_bucket == "story_from_bug":
        priority_match: bool | None = None if type_match else False
        return BenchmarkRowScore(
            bucket=row.benchmark_bucket,
            issue_key=row.issue_key,
            success=type_match,
            predicted_issue_type=pred_type,
            predicted_priority=pred_pri,
            ground_truth_issue_type=gt_type,
            ground_truth_priority=None,
            type_match=type_match,
            priority_match=priority_match,
            notes=None,
        )

    gt_priority = _ground_truth_priority_bug_bucket(row, issue)

    if row.benchmark_bucket == "stable_bug":
        if gt_priority is None:
            return BenchmarkRowScore(
                bucket=row.benchmark_bucket,
                issue_key=row.issue_key,
                success=False,
                predicted_issue_type=pred_type,
                predicted_priority=pred_pri,
                ground_truth_issue_type=gt_type,
                ground_truth_priority=None,
                type_match=type_match,
                priority_match=False,
                notes="issue_priority_missing_or_invalid",
            )
        if not type_match:
            return BenchmarkRowScore(
                bucket=row.benchmark_bucket,
                issue_key=row.issue_key,
                success=False,
                predicted_issue_type=pred_type,
                predicted_priority=pred_pri,
                ground_truth_issue_type=gt_type,
                ground_truth_priority=gt_priority,
                type_match=False,
                priority_match=False,
                notes=None,
            )
        priority_match = pred_pri == gt_priority
        return BenchmarkRowScore(
            bucket=row.benchmark_bucket,
            issue_key=row.issue_key,
            success=priority_match,
            predicted_issue_type=pred_type,
            predicted_priority=pred_pri,
            ground_truth_issue_type=gt_type,
            ground_truth_priority=gt_priority,
            type_match=True,
            priority_match=priority_match,
            notes=None,
        )

    if row.benchmark_bucket == "misprioritized_bug":
        if gt_priority is None:
            return BenchmarkRowScore(
                bucket=row.benchmark_bucket,
                issue_key=row.issue_key,
                success=False,
                predicted_issue_type=pred_type,
                predicted_priority=pred_pri,
                ground_truth_issue_type=gt_type,
                ground_truth_priority=None,
                type_match=type_match,
                priority_match=False,
                notes="csv_priority_change_to_invalid",
            )
        if not type_match:
            return BenchmarkRowScore(
                bucket=row.benchmark_bucket,
                issue_key=row.issue_key,
                success=False,
                predicted_issue_type=pred_type,
                predicted_priority=pred_pri,
                ground_truth_issue_type=gt_type,
                ground_truth_priority=gt_priority,
                type_match=False,
                priority_match=False,
                notes=None,
            )
        priority_match = pred_pri == gt_priority
        return BenchmarkRowScore(
            bucket=row.benchmark_bucket,
            issue_key=row.issue_key,
            success=priority_match,
            predicted_issue_type=pred_type,
            predicted_priority=pred_pri,
            ground_truth_issue_type=gt_type,
            ground_truth_priority=gt_priority,
            type_match=True,
            priority_match=priority_match,
            notes=None,
        )

    msg = f"Unknown benchmark_bucket: {row.benchmark_bucket!r}"
    raise ValueError(msg)


def score_triage_outcome(
    row: BenchmarkCsvRow,
    issue: FetchedIssue,
    outcome: TriageRecommendation | TriageFailure,
) -> BenchmarkRowScore:
    """Score either a parsed recommendation or a structured triage failure."""
    if isinstance(outcome, TriageFailure):
        return _score_triage_failure(row, issue, outcome)
    return score_benchmark_prediction(row, issue, outcome)


def _score_triage_failure(
    row: BenchmarkCsvRow,
    issue: FetchedIssue,
    failure: TriageFailure,
) -> BenchmarkRowScore:
    gt_type = ground_truth_issue_type(row)
    gt_pri = _ground_truth_priority_bug_bucket(row, issue)
    if row.benchmark_bucket == "story_from_bug":
        pri: bool | None = False
    else:
        pri = False
    return BenchmarkRowScore(
        bucket=row.benchmark_bucket,
        issue_key=row.issue_key,
        success=False,
        predicted_issue_type=None,
        predicted_priority=None,
        ground_truth_issue_type=gt_type,
        ground_truth_priority=gt_pri,
        type_match=False,
        priority_match=pri,
        notes=f"{failure.category}:{failure.message}",
    )


def aggregate_bucket_summaries(scores: Sequence[BenchmarkRowScore]) -> list[BenchmarkBucketSummary]:
    """Per-bucket counts and accuracy."""
    grouped: dict[str, list[bool]] = defaultdict(list)
    for s in scores:
        grouped[s.bucket].append(s.success)
    return sorted(
        (
            BenchmarkBucketSummary(bucket=b, total=len(vals), successes=sum(1 for v in vals if v))
            for b, vals in grouped.items()
        ),
        key=lambda x: x.bucket,
    )


def aggregate_overall(scores: Sequence[BenchmarkRowScore]) -> BenchmarkOverallSummary:
    """Single accuracy over all rows."""
    total = len(scores)
    successes = sum(1 for s in scores if s.success)
    return BenchmarkOverallSummary(total=total, successes=successes)


def score_benchmark_skipped(row: BenchmarkCsvRow, *, reason: str) -> BenchmarkRowScore:
    """Row could not be triaged (e.g. Jira fetch error); counts as failure for aggregates."""
    gt_type = ground_truth_issue_type(row)
    return BenchmarkRowScore(
        bucket=row.benchmark_bucket,
        issue_key=row.issue_key,
        success=False,
        predicted_issue_type=None,
        predicted_priority=None,
        ground_truth_issue_type=gt_type,
        ground_truth_priority=None,
        type_match=False,
        priority_match=False,
        notes=reason,
    )


def confusion_matrix_issue_type(scores: Sequence[BenchmarkRowScore]) -> dict[tuple[str, str], int]:
    """Counts of (ground_truth_issue_type, predicted_issue_type).

    Predicted side uses ``"_failed"`` when the run did not produce a type.
    """
    counts: dict[tuple[str, str], int] = {}
    for s in scores:
        pred = s.predicted_issue_type or "_failed"
        key = (s.ground_truth_issue_type, pred)
        counts[key] = counts.get(key, 0) + 1
    return counts


def project_key_from_issue_key(issue_key: str) -> str:
    """Return Jira project key prefix (e.g. ``BC`` from ``BC-123``)."""
    if "-" not in issue_key:
        msg = f"Invalid issue key (expected PROJECT-NUMBER): {issue_key!r}"
        raise ValueError(msg)
    return issue_key.split("-", 1)[0].strip()


BENCHMARK_ISSUE_FETCH_CACHE_VERSION = 1


def ordered_unique_issue_keys(rows: Sequence[BenchmarkCsvRow]) -> list[str]:
    """Stable de-duplication of non-empty ``issue_key`` values in CSV row order."""
    return list(dict.fromkeys(k for k in (r.issue_key.strip() for r in rows) if k))


def write_benchmark_issue_fetch_cache(
    path: Path,
    *,
    issues: Mapping[str, FetchedIssue],
    failures: Mapping[str, str],
    fetched_at: str | None = None,
) -> None:
    """Persist fetched issues and fetch failures for benchmark replay or merge runs."""
    when = fetched_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload: dict[str, Any] = {
        "version": BENCHMARK_ISSUE_FETCH_CACHE_VERSION,
        "fetched_at": when,
        "issues": {k: v.model_dump() for k, v in issues.items()},
        "failures": dict(failures),
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def load_benchmark_issue_fetch_cache(
    path: Path,
) -> tuple[dict[str, FetchedIssue], dict[str, str], str] | None:
    """Load a cache file written by :func:`write_benchmark_issue_fetch_cache`.

    Returns ``(issues, failures, fetched_at)``, or ``None`` if the file is missing.
    Raises ``ValueError`` if the file exists but is not a supported cache document.
    """
    if not path.is_file():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        msg = f"Invalid issue cache (expected object): {path}"
        raise ValueError(msg)
    version = raw.get("version")
    if version != BENCHMARK_ISSUE_FETCH_CACHE_VERSION:
        msg = f"Unsupported issue cache version {version!r} in {path}"
        raise ValueError(msg)
    issues_raw = raw.get("issues")
    failures_raw = raw.get("failures")
    if not isinstance(issues_raw, dict) or not isinstance(failures_raw, dict):
        msg = f"Invalid issue cache shape in {path}"
        raise ValueError(msg)
    issues: dict[str, FetchedIssue] = {}
    for key, body in issues_raw.items():
        if not isinstance(key, str) or not isinstance(body, dict):
            msg = f"Invalid issue cache entry for {key!r} in {path}"
            raise ValueError(msg)
        issues[key] = FetchedIssue.model_validate(body)
    failures: dict[str, str] = {}
    for key, message in failures_raw.items():
        if not isinstance(key, str) or not isinstance(message, str):
            bad = f"Invalid failure entry for {key!r} in {path}"
            raise ValueError(bad)
        failures[key] = message
    fetched_at = raw.get("fetched_at")
    when = fetched_at if isinstance(fetched_at, str) else ""
    return issues, failures, when


def prefetch_jira_issues_for_keys(
    keys: Sequence[str],
    fetcher: JiraIssueFetcher,
) -> tuple[dict[str, FetchedIssue], dict[str, str]]:
    """Fetch each key once via ``fetcher``; failures are recorded per key without aborting."""
    issues: dict[str, FetchedIssue] = {}
    failures: dict[str, str] = {}
    for key in keys:
        try:
            issues[key] = fetcher.fetch(key, run_id=f"benchmark-prefetch-{uuid.uuid4()}")
        except JiraIssueFetchError as exc:
            failures[key] = str(exc)
    return issues, failures


def merge_cached_issues_with_fetch(
    keys: Sequence[str],
    fetcher: JiraIssueFetcher,
    *,
    cached_issues: Mapping[str, FetchedIssue],
    cached_failures: Mapping[str, str],
) -> tuple[dict[str, FetchedIssue], dict[str, str]]:
    """Start from cache entries, then HTTP-fetch any ``keys`` not yet present."""
    issues = dict(cached_issues)
    failures = dict(cached_failures)
    for key in keys:
        if key in issues or key in failures:
            continue
        try:
            issues[key] = fetcher.fetch(key, run_id=f"benchmark-prefetch-{uuid.uuid4()}")
        except JiraIssueFetchError as exc:
            failures[key] = str(exc)
    return issues, failures
