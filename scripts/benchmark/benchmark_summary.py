"""Post-hoc summary of benchmark JSONL rows produced by ``run_classification_benchmark``.

Pure helpers (no Jira/OpenRouter dependencies) so a folder of ``rows_*.jsonl``
files can be analysed offline. Reuses the scoring/aggregation primitives from
:mod:`scripts.benchmark.classification_benchmark` to stay consistent with ``summary.json``.
"""

from __future__ import annotations

import json
import math
import re
import statistics
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from scripts.benchmark.classification_benchmark import (
    BenchmarkBucketSummary,
    BenchmarkOverallSummary,
    BenchmarkRowScore,
    aggregate_bucket_summaries,
    aggregate_overall,
    confusion_matrix_issue_type,
)
from triage_service.core.triage_recommendation_parser import IssueTypeLiteral

_VALID_ISSUE_TYPES: frozenset[str] = frozenset({"Bug", "Story"})


@dataclass(frozen=True)
class FailureCounts:
    """Failure counts within a single model's run, grouped by ``outcome.category``."""

    total: int
    by_category: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class LatencyStats:
    """Latency summary stats in milliseconds."""

    count: int
    total_ms: float
    mean_ms: float
    median_ms: float
    p95_ms: float
    min_ms: float
    max_ms: float


@dataclass(frozen=True)
class ModelRunSummary:
    """Aggregate stats for one ``rows_<model>.jsonl`` file."""

    model: str
    source_file: Path
    row_count: int
    buckets: tuple[BenchmarkBucketSummary, ...]
    overall: BenchmarkOverallSummary
    confusion_issue_type: Mapping[tuple[str, str], int]
    failures: FailureCounts
    latency: LatencyStats
    priority_evaluated: int
    priority_matches: int

    @property
    def priority_accuracy(self) -> float:
        if self.priority_evaluated <= 0:
            return 0.0
        return self.priority_matches / self.priority_evaluated


def load_jsonl_rows(path: Path) -> list[dict[str, Any]]:
    """Parse a JSONL file; raise ``ValueError`` with the file path on bad lines."""
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for lineno, raw in enumerate(handle, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                msg = f"{path}: invalid JSON on line {lineno}: {exc.msg}"
                raise ValueError(msg) from exc
            if not isinstance(obj, dict):
                msg = f"{path}: line {lineno} is not a JSON object"
                raise ValueError(msg)
            rows.append(cast(dict[str, Any], obj))
    return rows


def _coerce_issue_type(value: Any) -> IssueTypeLiteral:
    text = str(value or "").strip()
    if text not in _VALID_ISSUE_TYPES:
        msg = f"ground_truth_issue_type must be 'Bug' or 'Story'; got {value!r}"
        raise ValueError(msg)
    return cast(IssueTypeLiteral, text)


def benchmark_row_score_from_record(record: Mapping[str, Any]) -> BenchmarkRowScore:
    """Rehydrate the ``BenchmarkRowScore`` written to a benchmark JSONL row."""
    score = record.get("score")
    if not isinstance(score, Mapping):
        msg = "record is missing required 'score' object"
        raise ValueError(msg)
    return BenchmarkRowScore(
        bucket=str(score.get("bucket") or ""),
        issue_key=str(score.get("issue_key") or ""),
        success=bool(score.get("success")),
        predicted_issue_type=_optional_str(score.get("predicted_issue_type")),
        predicted_priority=_optional_str(score.get("predicted_priority")),
        ground_truth_issue_type=_coerce_issue_type(score.get("ground_truth_issue_type")),
        ground_truth_priority=_optional_str(score.get("ground_truth_priority")),
        type_match=bool(score.get("type_match")),
        priority_match=_optional_bool(score.get("priority_match")),
        notes=_optional_str(score.get("notes")),
    )


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)


def compute_latency_stats(latencies_ms: Sequence[float]) -> LatencyStats:
    """Compute count/total/mean/median/p95/min/max for ``latencies_ms``."""
    if not latencies_ms:
        return LatencyStats(0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    values = sorted(float(v) for v in latencies_ms)
    total = math.fsum(values)
    return LatencyStats(
        count=len(values),
        total_ms=total,
        mean_ms=total / len(values),
        median_ms=statistics.median(values),
        p95_ms=_percentile(values, 0.95),
        min_ms=values[0],
        max_ms=values[-1],
    )


def _percentile(sorted_values: Sequence[float], fraction: float) -> float:
    """Linear-interpolated percentile on already-sorted values (``fraction`` in [0, 1])."""
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    pos = fraction * (len(sorted_values) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return float(sorted_values[lo])
    frac = pos - lo
    return float(sorted_values[lo]) + (float(sorted_values[hi]) - float(sorted_values[lo])) * frac


def compute_failure_counts(records: Sequence[Mapping[str, Any]]) -> FailureCounts:
    """Count ``outcome.kind == 'failure'`` rows, grouped by ``outcome.category``."""
    by_category: dict[str, int] = defaultdict(int)
    total = 0
    for rec in records:
        outcome = rec.get("outcome")
        if not isinstance(outcome, Mapping):
            continue
        if outcome.get("kind") != "failure":
            continue
        total += 1
        category = str(outcome.get("category") or "unknown")
        by_category[category] += 1
    return FailureCounts(total=total, by_category=dict(by_category))


_MODEL_FILENAME_RE = re.compile(r"^rows_(.+)$")


def _model_from_filename(source_file: Path) -> str:
    stem = source_file.stem
    match = _MODEL_FILENAME_RE.match(stem)
    if not match:
        return stem
    encoded = match.group(1)
    return encoded.replace("_", "/", 1).replace("_", ":")


def summarize_model_run(
    *,
    records: Sequence[Mapping[str, Any]],
    source_file: Path,
) -> ModelRunSummary:
    """Aggregate one model's JSONL records into a :class:`ModelRunSummary`."""
    scores = [benchmark_row_score_from_record(rec) for rec in records]
    bucket_summaries = tuple(aggregate_bucket_summaries(scores))
    overall = aggregate_overall(scores)
    confusion = confusion_matrix_issue_type(scores)
    failures = compute_failure_counts(records)
    latencies = [float(r.get("latency_ms") or 0.0) for r in records]
    latency = compute_latency_stats(latencies)

    priority_evaluated = sum(1 for s in scores if s.priority_match is not None)
    priority_matches = sum(1 for s in scores if s.priority_match is True)

    model = _first_model(records) or _model_from_filename(source_file)

    return ModelRunSummary(
        model=model,
        source_file=source_file,
        row_count=len(records),
        buckets=bucket_summaries,
        overall=overall,
        confusion_issue_type=confusion,
        failures=failures,
        latency=latency,
        priority_evaluated=priority_evaluated,
        priority_matches=priority_matches,
    )


def _first_model(records: Sequence[Mapping[str, Any]]) -> str | None:
    for rec in records:
        model = rec.get("model")
        if isinstance(model, str) and model:
            return model
    return None


def discover_row_files(folder: Path) -> list[Path]:
    """Return ``rows_*.jsonl`` files in ``folder`` (non-recursive), sorted by name."""
    if not folder.is_dir():
        msg = f"Not a directory: {folder}"
        raise NotADirectoryError(msg)
    return sorted(p for p in folder.glob("rows_*.jsonl") if p.is_file())


def format_summary_text(summary: ModelRunSummary) -> str:
    """Render a human-readable table for one model summary."""
    lines: list[str] = []
    lines.append(f"=== {summary.model} ===")
    lines.append(f"source: {summary.source_file}")
    lines.append(f"rows:   {summary.row_count}")
    lines.append("")
    lines.append(f"{'bucket':<22} {'n':>5} {'ok':>5} {'accuracy':>10}")
    for bucket in summary.buckets:
        lines.append(
            f"{bucket.bucket:<22} {bucket.total:5d} {bucket.successes:5d} "
            f"{bucket.accuracy:10.3f}"
        )
    lines.append(
        f"{'OVERALL':<22} {summary.overall.total:5d} {summary.overall.successes:5d} "
        f"{summary.overall.accuracy:10.3f}"
    )
    lines.append("")
    lines.append(
        f"priority accuracy: {summary.priority_matches}/{summary.priority_evaluated} "
        f"({summary.priority_accuracy:.3f})"
    )
    lines.append(
        f"latency_ms: total={summary.latency.total_ms:.0f} mean={summary.latency.mean_ms:.0f} "
        f"median={summary.latency.median_ms:.0f} p95={summary.latency.p95_ms:.0f} "
        f"min={summary.latency.min_ms:.0f} max={summary.latency.max_ms:.0f}"
    )
    lines.append(f"failures: {summary.failures.total}")
    for category, count in sorted(summary.failures.by_category.items()):
        lines.append(f"  - {category}: {count}")
    lines.append("confusion (ground_truth -> predicted):")
    for (gt, pred), count in sorted(summary.confusion_issue_type.items()):
        lines.append(f"  {gt} -> {pred}: {count}")
    return "\n".join(lines)


def summary_to_dict(summary: ModelRunSummary) -> dict[str, Any]:
    """Return a JSON-friendly dict for ``summary``."""
    return {
        "model": summary.model,
        "source_file": str(summary.source_file),
        "row_count": summary.row_count,
        "buckets": [
            {
                "bucket": b.bucket,
                "total": b.total,
                "successes": b.successes,
                "accuracy": b.accuracy,
            }
            for b in summary.buckets
        ],
        "overall": {
            "total": summary.overall.total,
            "successes": summary.overall.successes,
            "accuracy": summary.overall.accuracy,
        },
        "confusion_issue_type": {
            f"{gt}->{pred}": count
            for (gt, pred), count in sorted(summary.confusion_issue_type.items())
        },
        "priority": {
            "evaluated": summary.priority_evaluated,
            "matches": summary.priority_matches,
            "accuracy": summary.priority_accuracy,
        },
        "failures": {
            "total": summary.failures.total,
            "by_category": dict(sorted(summary.failures.by_category.items())),
        },
        "latency": {
            "count": summary.latency.count,
            "total_ms": summary.latency.total_ms,
            "mean_ms": summary.latency.mean_ms,
            "median_ms": summary.latency.median_ms,
            "p95_ms": summary.latency.p95_ms,
            "min_ms": summary.latency.min_ms,
            "max_ms": summary.latency.max_ms,
        },
    }


def folder_summary_to_dict(
    folder: Path,
    summaries: Sequence[ModelRunSummary],
) -> dict[str, Any]:
    """Combine per-model summaries into a folder-level aggregate dict."""
    return {
        "folder": str(folder),
        "model_count": len(summaries),
        "per_model": {s.model: summary_to_dict(s) for s in summaries},
    }
