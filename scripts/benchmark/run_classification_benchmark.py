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
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import find_dotenv, load_dotenv
from tqdm import tqdm

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src"
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from scripts.benchmark.classification_benchmark import (  # noqa: E402
    BenchmarkBucketSummary,
    BenchmarkCsvRow,
    BenchmarkImageStratumSummary,
    BenchmarkOverallSummary,
    BenchmarkRowScore,
    aggregate_bucket_summaries,
    aggregate_image_stratum_summaries,
    aggregate_overall,
    confusion_matrix_issue_type,
    load_benchmark_csv,
    load_benchmark_issue_fetch_cache,
    merge_cached_issues_with_fetch,
    ordered_unique_issue_keys,
    prefetch_jira_issues_for_keys,
    project_key_from_issue_key,
    resolve_benchmark_image_context_enabled,
    score_benchmark_skipped,
    score_triage_outcome,
    write_benchmark_issue_fetch_cache,
)
from triage_service.adapters.image_context_extractor import (  # noqa: E402
    ImageContextExtractionResult,
    ImageContextExtractor,
    build_image_context_extractor,
    issue_has_inline_images,
)
from triage_service.adapters.jira_issue_fetcher import (  # noqa: E402
    FetchedIssue,
    JiraIssueFetcher,
)
from triage_service.adapters.openrouter_inference_client import (  # noqa: E402
    OpenRouterInferenceClient,
)
from triage_service.core.settings import AppSettings, load_settings  # noqa: E402
from triage_service.core.policy_context import load_policy_context  # noqa: E402
from triage_service.core.triage_fallback import TriageFailure  # noqa: E402
from triage_service.core.triage_handler import NoOpTriageActionExecutor, TriageHandler  # noqa: E402
from triage_service.core.triage_recommendation_parser import TriageRecommendation  # noqa: E402

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


def _build_handler(
    settings: AppSettings,
    model: str,
    *,
    fetcher: JiraIssueFetcher,
    image_extractor: ImageContextExtractor,
) -> TriageHandler:
    policy = load_policy_context()
    inference = OpenRouterInferenceClient(settings, model_override=model)
    return TriageHandler(
        allowed_projects=settings.allowed_projects,
        fetcher=fetcher,
        inference=inference,
        policy=policy,
        executor=NoOpTriageActionExecutor(),
        image_context_extractor=image_extractor,
    )


def _image_context_fields(
    *,
    issue: FetchedIssue,
    extraction: ImageContextExtractionResult | None,
) -> dict[str, Any]:
    has_images = issue_has_inline_images(issue)
    if extraction is None:
        return {
            "has_images": has_images,
            "image_context": [],
            "image_context_attachments_considered": 0,
            "image_context_attachments_extracted": 0,
        }
    return {
        "has_images": has_images,
        "image_context": [ctx.model_dump(mode="json") for ctx in extraction.contexts],
        "image_context_attachments_considered": extraction.attachments_considered,
        "image_context_attachments_extracted": extraction.attachments_extracted,
    }


def _print_results_table(
    model: str,
    summaries: list[BenchmarkBucketSummary],
    overall: BenchmarkOverallSummary,
    image_strata: list[BenchmarkImageStratumSummary] | None = None,
) -> None:
    print(f"\n=== {model} ===")
    print(f"{'bucket':<22} {'n':>5} {'ok':>5} {'accuracy':>10}")
    for bucket_summary in summaries:
        print(
            f"{bucket_summary.bucket:<22} {bucket_summary.total:5d} "
            f"{bucket_summary.successes:5d} {bucket_summary.accuracy:10.3f}"
        )
    print(f"{'OVERALL':<22} {overall.total:5d} {overall.successes:5d} {overall.accuracy:10.3f}")
    if image_strata:
        print(f"{'stratum':<22} {'n':>5} {'ok':>5} {'accuracy':>10}")
        for stratum_summary in image_strata:
            print(
                f"{stratum_summary.stratum:<22} {stratum_summary.total:5d} "
                f"{stratum_summary.successes:5d} {stratum_summary.accuracy:10.3f}"
            )


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
    image_context_enabled: bool,
    image_extractor: ImageContextExtractor,
) -> tuple[
    list[BenchmarkRowScore],
    list[tuple[BenchmarkRowScore, bool]],
    dict[tuple[str, str], int],
    float,
]:
    fetcher = JiraIssueFetcher(settings)
    handler = _build_handler(
        settings,
        model,
        fetcher=fetcher,
        image_extractor=image_extractor,
    )
    scores: list[BenchmarkRowScore] = []
    scored_with_stratum: list[tuple[BenchmarkRowScore, bool]] = []
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
                scored_with_stratum.append((sc, False))
                record = {
                    "model": model,
                    "issue_key": row.issue_key,
                    "bucket": row.benchmark_bucket,
                    "latency_ms": round(elapsed_ms, 3),
                    "has_images": False,
                    "image_context": [],
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
                scored_with_stratum.append((sc, False))
                record = {
                    "model": model,
                    "issue_key": row.issue_key,
                    "bucket": row.benchmark_bucket,
                    "latency_ms": round(elapsed_ms, 3),
                    "has_images": False,
                    "image_context": [],
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
                scored_with_stratum.append((sc, False))
                record = {
                    "model": model,
                    "issue_key": row.issue_key,
                    "bucket": row.benchmark_bucket,
                    "latency_ms": round(elapsed_ms, 3),
                    "has_images": False,
                    "image_context": [],
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
            run_id = str(uuid.uuid4())
            image_extraction: ImageContextExtractionResult | None = None
            if image_context_enabled:
                image_extraction = image_extractor.extract(issue, run_id=run_id)
            outcome = handler.run_sync_on_fetched(
                issue=issue,
                project=project_key_from_issue_key(key),
                source=source,
                run_id=run_id,
                image_contexts=(
                    image_extraction.contexts if image_extraction is not None else None
                ),
                image_extraction=image_extraction,
            )
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            total_latency += elapsed_ms
            sc = score_triage_outcome(row, issue, outcome)
            scores.append(sc)
            has_images = issue_has_inline_images(issue)
            scored_with_stratum.append((sc, has_images))
            record = {
                "model": model,
                "issue_key": row.issue_key,
                "bucket": row.benchmark_bucket,
                "latency_ms": round(elapsed_ms, 3),
                "outcome": _serialize_outcome(outcome),
                "score": asdict(sc),
                **_image_context_fields(issue=issue, extraction=image_extraction),
            }
            jl.write(json.dumps(record, ensure_ascii=False) + "\n")
            if sleep_seconds > 0:
                time.sleep(sleep_seconds)
    confusion = confusion_matrix_issue_type(scores)
    return scores, scored_with_stratum, confusion, total_latency


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
        help="Comma-separated OpenRouter model ids (default: TRIAGE_TEXT_MODEL from env)",
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
        default="manual_trigger",
        help="Triage source string recorded on the handler path (default: manual_trigger)",
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
    image_group = parser.add_mutually_exclusive_group()
    image_group.add_argument(
        "--image-context",
        action="store_true",
        dest="image_context",
        default=None,
        help="Enable vision preprocessing for inline description images (overrides env)",
    )
    image_group.add_argument(
        "--no-image-context",
        action="store_false",
        dest="image_context",
        help="Disable vision preprocessing even when TRIAGE_IMAGE_CONTEXT_ENABLED is set",
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

    models = _parse_models(args.models, settings.triage_text_model)
    image_context_enabled = resolve_benchmark_image_context_enabled(
        cli_enable=args.image_context,
        settings_enabled=settings.triage_image_context_enabled,
    )
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = (args.output_dir / run_id).resolve()
    out.mkdir(parents=True, exist_ok=True)

    issue_cache_path = out / "fetched_issues_cache.json"
    unique_keys = ordered_unique_issue_keys(rows)
    fetcher = JiraIssueFetcher(settings)
    image_extractor = build_image_context_extractor(settings, fetcher)
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
        "image_context_enabled": image_context_enabled,
        "issue_fetch_cache": str(issue_cache_path),
        "per_model": {},
    }

    show_progress = not args.no_progress
    for model in models:
        _LOG.info("Benchmarking model=%s (%d rows)", model, len(rows))
        scores, scored_with_stratum, confusion, latency_sum_ms = _run_for_model(
            model=model,
            rows=rows,
            settings=settings,
            output_dir=out,
            sleep_seconds=max(0.0, args.sleep_seconds),
            source=args.source,
            show_progress=show_progress,
            issues_by_key=issues_by_key,
            fetch_failures_by_key=fetch_failures_by_key,
            image_context_enabled=image_context_enabled,
            image_extractor=image_extractor,
        )
        summaries = aggregate_bucket_summaries(scores)
        overall = aggregate_overall(scores)
        image_strata = aggregate_image_stratum_summaries(scored_with_stratum)
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
            "image_strata": [
                {
                    "stratum": s.stratum,
                    "total": s.total,
                    "successes": s.successes,
                    "accuracy": s.accuracy,
                }
                for s in image_strata
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
        _print_results_table(model, summaries, overall, image_strata)

    summary_path = out / "summary.json"
    summary_path.write_text(json.dumps(aggregate, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nWrote aggregate summary to {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
