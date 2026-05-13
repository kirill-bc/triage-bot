#!/usr/bin/env python3
"""Summarize all ``rows_*.jsonl`` files in a benchmark run folder.

Walks the given folder (non-recursive by default) and, for every
``rows_<model>.jsonl`` produced by ``run_classification_benchmark.py``, prints
per-bucket accuracy, overall accuracy, latency stats, the issue-type confusion
matrix, and a failure breakdown. With ``--output-json``, also writes a
folder-level aggregate JSON file.

Example::

    ./scripts/benchmark/summarize_benchmark_rows.py \\
        benchmark_runs/20260512T211133Z \\
        --output-json benchmark_runs/20260512T211133Z/rows_summary.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.benchmark.benchmark_summary import (  # noqa: E402
    ModelRunSummary,
    discover_row_files,
    folder_summary_to_dict,
    format_summary_text,
    load_jsonl_rows,
    summarize_model_run,
)


def _summarize_folder(folder: Path, *, recursive: bool) -> list[ModelRunSummary]:
    folders = [folder]
    if recursive:
        folders.extend(p for p in folder.rglob("*") if p.is_dir())
    summaries: list[ModelRunSummary] = []
    seen: set[Path] = set()
    for current in folders:
        try:
            files = discover_row_files(current)
        except NotADirectoryError:
            continue
        for jsonl_path in files:
            if jsonl_path in seen:
                continue
            seen.add(jsonl_path)
            records = load_jsonl_rows(jsonl_path)
            summaries.append(summarize_model_run(records=records, source_file=jsonl_path))
    summaries.sort(key=lambda s: (-s.overall.accuracy, s.model))
    return summaries


def _print_folder_overview(summaries: list[ModelRunSummary]) -> None:
    print(f"\n--- Folder overview ({len(summaries)} model runs, ranked by accuracy) ---")
    print(f"{'model':<48} {'rows':>5} {'ok':>5} {'acc':>7} {'fail':>5} {'mean_ms':>8}")
    for s in summaries:
        print(
            f"{s.model:<48} {s.overall.total:5d} {s.overall.successes:5d} "
            f"{s.overall.accuracy:7.3f} {s.failures.total:5d} "
            f"{s.latency.mean_ms:8.0f}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "folder",
        type=Path,
        help="Folder containing rows_*.jsonl files (e.g. a benchmark_runs/<timestamp> dir)",
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="Also descend into subdirectories",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=None,
        help="Optional path to write a folder-level aggregate JSON",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Skip the per-model detailed tables; print only the folder overview",
    )
    args = parser.parse_args()

    folder = args.folder.expanduser().resolve()
    if not folder.is_dir():
        print(f"Not a directory: {folder}", file=sys.stderr)
        return 2

    summaries = _summarize_folder(folder, recursive=args.recursive)
    if not summaries:
        print(f"No rows_*.jsonl files found under {folder}", file=sys.stderr)
        return 1

    if not args.quiet:
        for summary in summaries:
            print(format_summary_text(summary))
            print()

    _print_folder_overview(summaries)

    if args.output_json is not None:
        out = args.output_json.expanduser().resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = folder_summary_to_dict(folder, summaries)
        out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nWrote folder summary to {out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
