"""Bulk triage over a JQL issue list: inference only, results written to a file."""

from __future__ import annotations

import argparse
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from pydantic import ValidationError
from tqdm import tqdm

from triage_service.adapters.image_context_extractor import build_cli_image_context_summary
from triage_service.adapters.zendesk_context_cli import build_cli_zendesk_context_summary
from triage_service.adapters.jira_jql_search import (
    JiraJqlSearchError,
    JiraSearchIssueRef,
    search_issues_by_jql,
)
from triage_service.core.settings import AppSettings
from triage_service.core.triage_fallback import TriageFailure
from triage_service.core.triage_handler import TriageRunner, build_default_triage_handler
from triage_manual_cli import (
    build_triage_cli_result_payload,
    infer_project_from_issue_key,
    run_cli_triage,
)

_ROOT = Path(__file__).resolve().parent


@dataclass(frozen=True)
class BulkTriageIssueRow:
    """One issue row in the bulk triage output file."""

    issue_key: str
    project: str
    current_issue_type: str
    current_priority: str | None
    status: str
    recommendation: dict[str, Any] | None = None
    classification: dict[str, Any] | None = None
    priority: dict[str, Any] | None = None
    failure: dict[str, Any] | None = None
    image_context: dict[str, Any] | None = None
    zendesk_context: dict[str, Any] | None = None


def _issue_row_from_triage(
    ref: JiraSearchIssueRef,
    *,
    project: str,
    payload: dict[str, Any],
) -> BulkTriageIssueRow:
    status = str(payload.get("status") or "")
    if status == "failed":
        return BulkTriageIssueRow(
            issue_key=ref.issue_key,
            project=project,
            current_issue_type=ref.issue_type,
            current_priority=ref.priority,
            status="failed",
            failure=payload.get("failure") if isinstance(payload.get("failure"), dict) else None,
            image_context=(
                payload.get("image_context")
                if isinstance(payload.get("image_context"), dict)
                else None
            ),
            zendesk_context=(
                payload.get("zendesk_context")
                if isinstance(payload.get("zendesk_context"), dict)
                else None
            ),
        )
    return BulkTriageIssueRow(
        issue_key=ref.issue_key,
        project=project,
        current_issue_type=ref.issue_type,
        current_priority=ref.priority,
        status="completed",
        recommendation=(
            payload.get("recommendation")
            if isinstance(payload.get("recommendation"), dict)
            else None
        ),
        classification=(
            payload.get("classification")
            if isinstance(payload.get("classification"), dict)
            else None
        ),
        priority=payload.get("priority") if isinstance(payload.get("priority"), dict) else None,
        image_context=(
            payload.get("image_context")
            if isinstance(payload.get("image_context"), dict)
            else None
        ),
        zendesk_context=(
            payload.get("zendesk_context")
            if isinstance(payload.get("zendesk_context"), dict)
            else None
        ),
    )


@dataclass(frozen=True)
class _BulkTriageWorkerConfig:
    """Loop-invariant settings shared read-only across worker threads."""

    settings: AppSettings
    runner: TriageRunner | None
    apply_to_jira: bool
    post_mismatch_comments: bool
    auto_apply_deescalation: bool | None
    auto_apply_escalation: bool | None
    auto_apply_bug_to_story: bool | None


class _BulkTriageRunnerPool:
    """Resolves one ``TriageRunner`` per worker thread (or reuses an injected runner).

    An explicitly injected ``runner`` (tests, single-process reuse) is shared across
    workers as-is. Otherwise each worker thread lazily builds and keeps its own full
    runner (own Jira/OpenRouter/Zendesk clients) -- the same pattern already proven
    safe under concurrent load by ``scripts/debug_concurrent_triage_probe.py``.
    """

    def __init__(self, config: _BulkTriageWorkerConfig) -> None:
        self._config = config
        self._thread_local = threading.local()
        self._built: list[TriageRunner] = []
        self._lock = threading.Lock()

    def resolve(self) -> TriageRunner:
        if self._config.runner is not None:
            return self._config.runner
        cached = getattr(self._thread_local, "runner", None)
        if cached is not None:
            return cast(TriageRunner, cached)
        built = build_default_triage_handler(
            post_mismatch_comments=self._config.post_mismatch_comments,
            apply_to_jira=self._config.apply_to_jira,
            auto_apply_deescalation=self._config.auto_apply_deescalation,
            auto_apply_escalation=self._config.auto_apply_escalation,
            auto_apply_bug_to_story=self._config.auto_apply_bug_to_story,
        )
        self._thread_local.runner = built
        with self._lock:
            self._built.append(built)
        return built

    def flush_all(self) -> None:
        runners: list[TriageRunner] = (
            [self._config.runner] if self._config.runner is not None else self._built
        )
        for candidate in runners:
            flush = getattr(candidate, "flush_inference_telemetry", None)
            if callable(flush):
                flush()


def _process_bulk_triage_ref(
    ref: JiraSearchIssueRef,
    *,
    config: _BulkTriageWorkerConfig,
    runner_pool: _BulkTriageRunnerPool,
) -> BulkTriageIssueRow:
    project = infer_project_from_issue_key(ref.issue_key)
    active_runner = runner_pool.resolve()
    result = run_cli_triage(
        ref.issue_key,
        project=project,
        runner=active_runner,
        post_mismatch_comments=config.post_mismatch_comments,
        apply_to_jira=config.apply_to_jira,
        auto_apply_deescalation=config.auto_apply_deescalation,
        auto_apply_escalation=config.auto_apply_escalation,
        auto_apply_bug_to_story=config.auto_apply_bug_to_story,
    )
    image_context = build_cli_image_context_summary(
        enabled=config.settings.triage_image_context_enabled,
        extraction=result.image_extraction,
    )
    zendesk_context = build_cli_zendesk_context_summary(
        enabled=bool(getattr(config.settings, "triage_zendesk_context_enabled", False)),
        enrichment=result.zendesk_context,
    )
    outcome = result.outcome
    if isinstance(outcome, TriageFailure):
        payload: dict[str, Any] = {
            "status": "failed",
            "failure": outcome.model_dump(),
            "image_context": image_context,
            "zendesk_context": zendesk_context,
        }
    else:
        payload = build_triage_cli_result_payload(
            result,
            image_context=image_context,
            zendesk_context=zendesk_context,
        )
    row = _issue_row_from_triage(ref, project=project, payload=payload)
    return row


def run_bulk_triage(
    refs: list[JiraSearchIssueRef],
    *,
    settings: AppSettings,
    runner: TriageRunner | None = None,
    apply_to_jira: bool = False,
    post_mismatch_comments: bool = False,
    auto_apply_deescalation: bool | None = None,
    auto_apply_escalation: bool | None = None,
    auto_apply_bug_to_story: bool | None = None,
    show_progress: bool | None = None,
    concurrency: int = 1,
) -> list[BulkTriageIssueRow]:
    """Triage each issue ref (bounded worker pool) and return rows in input order."""
    progress = sys.stderr.isatty() if show_progress is None else show_progress
    worker_count = max(1, concurrency)
    config = _BulkTriageWorkerConfig(
        settings=settings,
        runner=runner,
        apply_to_jira=apply_to_jira,
        post_mismatch_comments=post_mismatch_comments,
        auto_apply_deescalation=auto_apply_deescalation,
        auto_apply_escalation=auto_apply_escalation,
        auto_apply_bug_to_story=auto_apply_bug_to_story,
    )
    runner_pool = _BulkTriageRunnerPool(config)
    rows: list[BulkTriageIssueRow | None] = [None] * len(refs)
    progress_bar = tqdm(
        total=len(refs),
        desc="Triage",
        unit="issue",
        disable=not progress,
        file=sys.stderr,
        dynamic_ncols=True,
    )
    try:
        with ThreadPoolExecutor(max_workers=worker_count) as pool:
            future_to_index = {
                pool.submit(
                    _process_bulk_triage_ref,
                    ref,
                    config=config,
                    runner_pool=runner_pool,
                ): index
                for index, ref in enumerate(refs)
            }
            for future in as_completed(future_to_index):
                index = future_to_index[future]
                rows[index] = future.result()
                if progress:
                    progress_bar.set_postfix_str(refs[index].issue_key, refresh=False)
                progress_bar.update(1)
    finally:
        progress_bar.close()
    runner_pool.flush_all()
    return [row for row in rows if row is not None]


def _write_report(
    path: Path,
    *,
    jql: str,
    rows: list[BulkTriageIssueRow],
    apply_to_jira: bool,
) -> None:
    completed = sum(1 for row in rows if row.status == "completed")
    failed = len(rows) - completed
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "jql": jql,
        "apply_to_jira": apply_to_jira,
        "issue_count": len(rows),
        "completed_count": completed,
        "failed_count": failed,
        "results": [asdict(row) for row in rows],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    """Run bulk triage for issues matched by JQL; write JSON report to ``--output``."""
    parser = argparse.ArgumentParser(
        description=(
            "Run triage for each issue returned by a JQL query and write results to a file. "
            "By default does not modify Jira (no labels, no comments)."
        ),
    )
    parser.add_argument(
        "--jql",
        required=True,
        help="Jira JQL selecting issues to triage (e.g. project = TJC AND issuetype = Bug).",
    )
    parser.add_argument(
        "-o",
        "--output",
        required=True,
        help="Path to JSON output file.",
    )
    parser.add_argument(
        "--max-results",
        "--limit",
        type=int,
        default=50,
        dest="max_results",
        metavar="N",
        help=(
            "Maximum issues to fetch from JQL and triage (default: 50). "
            "Passed to Jira search as maxResults per request (capped at 100 per page)."
        ),
    )
    parser.add_argument(
        "--jql-page-size",
        type=int,
        default=None,
        metavar="N",
        help=(
            "Jira API maxResults per search page (default: min(50, --max-results); "
            "max 100)."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Apply triagebot labels in Jira after each issue (still no mismatch comments "
            "unless --comment)."
        ),
    )
    parser.add_argument(
        "--comment",
        action="store_true",
        help="Post mismatch comments when --apply is set (production-like side effects).",
    )
    parser.add_argument(
        "--auto-apply-deescalation",
        action="store_true",
        help=(
            "When --apply is set, directly update Jira priority for less-urgent "
            "recommendations (deescalations)."
        ),
    )
    parser.add_argument(
        "--auto-apply-escalation",
        action="store_true",
        help=(
            "When --apply is set, directly update Jira priority for more-urgent "
            "recommendations (escalations)."
        ),
    )
    parser.add_argument(
        "--auto-apply-bug-to-story",
        action="store_true",
        help=(
            "When --apply is set, directly update Jira issue type when recommendation "
            "is Bug -> Story."
        ),
    )
    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="Disable the tqdm progress bar (default: on when stderr is a TTY).",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=4,
        metavar="N",
        help=(
            "Number of issues to triage in parallel (default: 4). Each worker thread "
            "builds its own Jira/OpenRouter/Zendesk clients; use 1 for sequential."
        ),
    )
    ns = parser.parse_args(argv)

    if ns.max_results < 1:
        print("--max-results must be at least 1.", file=sys.stderr)
        return 2
    if ns.concurrency < 1:
        print("--concurrency must be at least 1.", file=sys.stderr)
        return 2

    dotenv_path = _ROOT / ".env"
    try:
        from triage_service.core.settings import load_settings

        settings: AppSettings = load_settings(
            env_file=dotenv_path if dotenv_path.is_file() else None,
        )
    except ValidationError as exc:
        print(f"Settings error: {exc}", file=sys.stderr)
        return 2

    try:
        refs = search_issues_by_jql(
            settings,
            ns.jql,
            max_results=ns.max_results,
            jql_page_size=ns.jql_page_size,
        )
    except JiraJqlSearchError as exc:
        print(f"JQL search error: {exc}", file=sys.stderr)
        return 2

    if not refs:
        print("No issues matched the JQL query.", file=sys.stderr)
        return 1

    apply_to_jira = bool(ns.apply)
    post_mismatch_comments = bool(ns.comment) and apply_to_jira
    rows = run_bulk_triage(
        refs,
        settings=settings,
        apply_to_jira=apply_to_jira,
        post_mismatch_comments=post_mismatch_comments,
        auto_apply_deescalation=ns.auto_apply_deescalation or None,
        auto_apply_escalation=ns.auto_apply_escalation or None,
        auto_apply_bug_to_story=ns.auto_apply_bug_to_story or None,
        show_progress=None if not ns.no_progress else False,
        concurrency=ns.concurrency,
    )
    output_path = Path(ns.output)
    _write_report(
        output_path,
        jql=ns.jql.strip(),
        rows=rows,
        apply_to_jira=apply_to_jira,
    )
    failed = sum(1 for row in rows if row.status == "failed")
    print(
        f"Wrote {len(rows)} result(s) to {output_path} "
        f"({failed} failed, {len(rows) - failed} completed).",
        file=sys.stderr,
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
