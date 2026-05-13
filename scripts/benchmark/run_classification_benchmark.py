#!/usr/bin/env python3
"""Evaluate triage models on ``data/issue_benchmark_dataset.csv`` (fetch → classify → priority).

Each Jira issue is fetched **once** per run, written to ``fetched_issues_cache.json`` under the
run output folder, and reused for every OpenRouter model (no duplicate Jira GETs per ticket).

Requires ``JIRA_*`` and ``OPENROUTER_*`` credentials like the main app. Uses a NoOp Jira executor
so benchmark runs never post comments or labels.

Example::

    ./scripts/benchmark/run_classification_benchmark.py \\
        --models openai/gpt-4o-mini,anthropic/claude-3-haiku \\
        --output-dir ./benchmark_runs
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import find_dotenv, load_dotenv
from tqdm import tqdm

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from classification_benchmark import (  # noqa: E402
    BenchmarkBucketSummary,
    BenchmarkCsvRow,
    BenchmarkOverallSummary,
    BenchmarkRowScore,
    aggregate_bucket_summaries,
    aggregate_overall,
    confusion_matrix_issue_type,
    load_benchmark_csv,
    load_benchmark_issue_fetch_cache,
    merge_cached_issues_with_fetch,
    ordered_unique_issue_keys,
    prefetch_jira_issues_for_keys,
    project_key_from_issue_key,
    score_benchmark_skipped,
    score_triage_outcome,
    write_benchmark_issue_fetch_cache,
)
from core_config import load_triage_core_config  # noqa: E402
from jira_issue_fetcher import FetchedIssue, JiraIssueFetcher  # noqa: E402
from openrouter_inference_client import OpenRouterInferenceClient  # noqa: E402
from policy_context import load_policy_context  # noqa: E402
from settings import AppSettings, load_settings  # noqa: E402
from triage_fallback import TriageFailure  # noqa: E402
from triage_handler import NoOpTriageActionExecutor, TriageHandler  # noqa: E402
from triage_recommendation_parser import TriageRecommendation  # noqa: E402

_LOG = logging.getLogger(__name__)


def _configure_script_logging(*, verbose: bool) -> None:
    """Configure logging; keep HTTP client libraries quiet.

    At DEBUG they log every request/response (very noisy).
    """
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")
    for name in ("httpx", "httpcore", "urllib3"):
        logging.getLogger(name).setLevel(logging.WARNING)


def _serialize_outcome(outcome: TriageRecommendation | TriageFailure) -> dict[str, Any]:
    if isinstance(outcome, TriageFailure):
        return {"kind": "failure", "category": outcome.category, "message": outcome.message}
    return {"kind": "ok", **outcome.model_dump()}


def _parse_models(raw: str | None, default_model: str) -> list[str]:
    if raw is None or not str(raw).strip():
        return [default_model]
    return [part.strip() for part in str(raw).split(",") if part.strip()]


def _build_handler(settings: AppSettings, model: str) -> TriageHandler:
    core = load_triage_core_config()
    policy = load_policy_context()
    fetcher = JiraIssueFetcher(settings)
    inference = OpenRouterInferenceClient(settings, model_override=model)
    return TriageHandler(
        allowed_projects=core.allowed_projects,
        fetcher=fetcher,
        inference=inference,
        policy=policy,
        executor=NoOpTriageActionExecutor(),
    )


def _print_results_table(
    model: str,
    summaries: list[BenchmarkBucketSummary],
    overall: BenchmarkOverallSummary,
) -> None:
    print(f"\n=== {model} ===")
    print(f"{'bucket':<22} {'n':>5} {'ok':>5} {'accuracy':>10}")
    for s in summaries:
        print(f"{s.bucket:<22} {s.total:5d} {s.successes:5d} {s.accuracy:10.3f}")
    print(f"{'OVERALL':<22} {overall.total:5d} {overall.successes:5d} {overall.accuracy:10.3f}")


def _run_for_model(
    *,
    model: str,
    rows: list[BenchmarkCsvRow],
    settings: AppSettings,
    output_dir: Path,
    sleep_seconds: float,
    source: str,
    show_progress: bool,
    issues_by_key: dict[str, FetchedIssue],
    fetch_failures_by_key: dict[str, str],
) -> tuple[list[BenchmarkRowScore], dict[tuple[str, str], int], float]:
    handler = _build_handler(settings, model)
    scores: list[BenchmarkRowScore] = []
    total_latency = 0.0
    jsonl_path = output_dir / f"rows_{_safe_model_filename(model)}.jsonl"
    row_iter = tqdm(
        rows,
        desc=model,
        unit="issue",
        disable=not show_progress,
        file=sys.stderr,
        dynamic_ncols=True,
    )
    with jsonl_path.open("w", encoding="utf-8") as jl:
        for row in row_iter:
            t0 = time.perf_counter()
            key = (row.issue_key or "").strip()
            if not key:
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                total_latency += elapsed_ms
                sc = score_benchmark_skipped(row, reason="empty_issue_key")
                scores.append(sc)
                record = {
                    "model": model,
                    "issue_key": row.issue_key,
                    "bucket": row.benchmark_bucket,
                    "latency_ms": round(elapsed_ms, 3),
                    "outcome": {
                        "kind": "failure",
                        "category": "empty_issue_key",
                        "message": "CSV issue_key is empty",
                    },
                    "score": asdict(sc),
                }
                jl.write(json.dumps(record, ensure_ascii=False) + "\n")
                continue
            if key in fetch_failures_by_key:
                err = fetch_failures_by_key[key]
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                total_latency += elapsed_ms
                sc = score_benchmark_skipped(row, reason=f"jira_fetch_failed:{err}")
                scores.append(sc)
                record = {
                    "model": model,
                    "issue_key": row.issue_key,
                    "bucket": row.benchmark_bucket,
                    "latency_ms": round(elapsed_ms, 3),
                    "outcome": {
                        "kind": "failure",
                        "category": "jira_fetch_failed",
                        "message": err,
                    },
                    "score": asdict(sc),
                }
                jl.write(json.dumps(record, ensure_ascii=False) + "\n")
                _LOG.warning("fetch failed for %s: %s", key, err)
                continue
            if key not in issues_by_key:
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                total_latency += elapsed_ms
                sc = score_benchmark_skipped(row, reason="issue_missing_from_prefetch_cache")
                scores.append(sc)
                record = {
                    "model": model,
                    "issue_key": row.issue_key,
                    "bucket": row.benchmark_bucket,
                    "latency_ms": round(elapsed_ms, 3),
                    "outcome": {
                        "kind": "failure",
                        "category": "prefetch_miss",
                        "message": key,
                    },
                    "score": asdict(sc),
                }
                jl.write(json.dumps(record, ensure_ascii=False) + "\n")
                _LOG.error("issue %s missing from prefetch maps (internal error)", key)
                continue
            issue = issues_by_key[key]
            outcome = handler.run_sync_on_fetched(
                issue=issue,
                project=project_key_from_issue_key(key),
                source=source,
            )
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            total_latency += elapsed_ms
            sc = score_triage_outcome(row, issue, outcome)
            scores.append(sc)
            record = {
                "model": model,
                "issue_key": row.issue_key,
                "bucket": row.benchmark_bucket,
                "latency_ms": round(elapsed_ms, 3),
                "outcome": _serialize_outcome(outcome),
                "score": asdict(sc),
            }
            jl.write(json.dumps(record, ensure_ascii=False) + "\n")
            if sleep_seconds > 0:
                time.sleep(sleep_seconds)
    confusion = confusion_matrix_issue_type(scores)
    return scores, confusion, total_latency


def _safe_model_filename(model: str) -> str:
    return model.replace("/", "_").replace(":", "_")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=_REPO_ROOT / "data" / "issue_benchmark_dataset.csv",
        help="Path to benchmark CSV (default: repo data/issue_benchmark_dataset.csv)",
    )
    parser.add_argument(
        "--models",
        type=str,
        default=None,
        help="Comma-separated OpenRouter model ids (default: OPENROUTER_MODEL from env)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Directory to write JSONL + summary JSON (a timestamped subfolder is created)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process only the first N rows (debug / smoke)",
    )
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=0.0,
        help="Pause between issues (rate limiting)",
    )
    parser.add_argument(
        "--source",
        type=str,
        default="manual_cli",
        help="Triage source string recorded on the handler path (default: manual_cli)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help=(
            "DEBUG logging for this script (HTTP libraries stay at WARNING; "
            "no per-request noise)"
        ),
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help=(
            "WARNING and above only (hides per-model INFO lines; "
            "progress bar still updates if enabled)"
        ),
    )
    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="Disable the per-model progress bar",
    )
    parser.add_argument(
        "--fetch-cache-input",
        type=Path,
        default=None,
        help=(
            "Optional JSON cache from a prior run (or partial cache). "
            "Keys already present are reused; only missing keys are fetched from Jira."
        ),
    )
    parser.add_argument(
        "--http-debug",
        action="store_true",
        help=(
            "Allow httpx/httpcore DEBUG logs (very noisy; for troubleshooting only)"
        ),
    )
    args = parser.parse_args()

    if args.quiet and args.verbose:
        print("Cannot combine --quiet and --verbose", file=sys.stderr)
        return 2

    _configure_script_logging(verbose=args.verbose)
    if args.quiet:
        logging.getLogger().setLevel(logging.WARNING)
        _LOG.setLevel(logging.WARNING)
    if args.http_debug:
        for name in ("httpx", "httpcore", "urllib3"):
            logging.getLogger(name).setLevel(logging.DEBUG)
    load_dotenv(find_dotenv(usecwd=True), override=False)
    settings = load_settings()

    rows = load_benchmark_csv(args.dataset)
    if args.limit is not None:
        rows = rows[: max(0, args.limit)]

    models = _parse_models(args.models, settings.openrouter_model)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = (args.output_dir / run_id).resolve()
    out.mkdir(parents=True, exist_ok=True)

    issue_cache_path = out / "fetched_issues_cache.json"
    unique_keys = ordered_unique_issue_keys(rows)
    fetcher = JiraIssueFetcher(settings)
    if args.fetch_cache_input is not None:
        cache_in = args.fetch_cache_input.expanduser().resolve()
        loaded = load_benchmark_issue_fetch_cache(cache_in)
        if loaded is None:
            print(f"Issue fetch cache not found: {cache_in}", file=sys.stderr)
            return 2
        cached_issues, cached_failures, _ = loaded
        cache_hits = sum(1 for k in unique_keys if k in cached_issues or k in cached_failures)
        issues_by_key, fetch_failures_by_key = merge_cached_issues_with_fetch(
            unique_keys,
            fetcher,
            cached_issues=cached_issues,
            cached_failures=cached_failures,
        )
        _LOG.info(
            "Merged fetch cache from %s (%d/%d keys from cache, rest fetched if needed)",
            cache_in,
            cache_hits,
            len(unique_keys),
        )
    else:
        _LOG.info("Prefetching %d unique Jira issues (one GET per key)", len(unique_keys))
        key_iter = tqdm(
            unique_keys,
            desc="jira_fetch",
            unit="key",
            disable=args.no_progress,
            file=sys.stderr,
            dynamic_ncols=True,
        )
        issues_by_key, fetch_failures_by_key = prefetch_jira_issues_for_keys(key_iter, fetcher)
        for key, err in fetch_failures_by_key.items():
            _LOG.warning("fetch failed for %s: %s", key, err)

    write_benchmark_issue_fetch_cache(
        issue_cache_path,
        issues=issues_by_key,
        failures=fetch_failures_by_key,
    )
    _LOG.info(
        "Wrote issue fetch cache to %s (%d ok, %d failed)",
        issue_cache_path,
        len(issues_by_key),
        len(fetch_failures_by_key),
    )

    aggregate: dict[str, Any] = {
        "run_id": run_id,
        "dataset": str(args.dataset.resolve()),
        "row_count": len(rows),
        "models": models,
        "issue_fetch_cache": str(issue_cache_path),
        "per_model": {},
    }

    show_progress = not args.no_progress
    for model in models:
        _LOG.info("Benchmarking model=%s (%d rows)", model, len(rows))
        scores, confusion, latency_sum_ms = _run_for_model(
            model=model,
            rows=rows,
            settings=settings,
            output_dir=out,
            sleep_seconds=max(0.0, args.sleep_seconds),
            source=args.source,
            show_progress=show_progress,
            issues_by_key=issues_by_key,
            fetch_failures_by_key=fetch_failures_by_key,
        )
        summaries = aggregate_bucket_summaries(scores)
        overall = aggregate_overall(scores)
        aggregate["per_model"][model] = {
            "buckets": [
                {
                    "bucket": s.bucket,
                    "total": s.total,
                    "successes": s.successes,
                    "accuracy": s.accuracy,
                }
                for s in summaries
            ],
            "overall": {
                "total": overall.total,
                "successes": overall.successes,
                "accuracy": overall.accuracy,
            },
            "confusion_issue_type": {f"{a}->{b}": c for (a, b), c in confusion.items()},
            "latency_total_ms": round(latency_sum_ms, 3),
            "jsonl": str(out / f"rows_{_safe_model_filename(model)}.jsonl"),
        }
        _print_results_table(model, summaries, overall)

    summary_path = out / "summary.json"
    summary_path.write_text(json.dumps(aggregate, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nWrote aggregate summary to {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
