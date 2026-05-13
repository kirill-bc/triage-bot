"""Build a minimal CSV benchmark set: issue keys plus last priority/type changelog transitions."""

from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
import time
from typing import Any

import httpx
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True), override=False)

_ATLASSIAN_GATEWAY = "https://api.atlassian.com/ex/jira"
_CLOUD_ID = (os.environ.get("JIRA_CLOUD_ID") or "").strip()
JIRA_URL = f"{_ATLASSIAN_GATEWAY}/{_CLOUD_ID}" if _CLOUD_ID else ""
EMAIL = os.environ.get("JIRA_USER_EMAIL")
API_TOKEN = os.environ.get("JIRA_API_KEY")

BUCKET_MISPRIORITIZED = "misprioritized_bug"
BUCKET_STORY_FROM_BUG = "story_from_bug"
BUCKET_STABLE_BUG = "stable_bug"

CSV_COLUMNS = (
    "benchmark_bucket",
    "issue_key",
    "priority_change_from",
    "priority_change_to",
    "issue_type_change_from",
    "issue_type_change_to",
)

_LOG = logging.getLogger(__name__)


class MinIntervalPacer:
    """Ensures at least `interval_seconds` between consecutive HTTP completions."""

    def __init__(self, interval_seconds: float) -> None:
        self._interval = max(0.0, interval_seconds)
        self._last_end: float | None = None

    def before_request(self) -> None:
        if self._interval <= 0:
            return
        if self._last_end is None:
            return
        elapsed = time.monotonic() - self._last_end
        wait = self._interval - elapsed
        if wait > 0:
            time.sleep(wait)

    def after_response(self) -> None:
        self._last_end = time.monotonic()


def _require_credentials() -> None:
    if not EMAIL or not API_TOKEN or not JIRA_URL:
        print(
            "Missing Jira credentials. Set JIRA_USER_EMAIL and JIRA_API_KEY in your environment "
            "or `.env`, and set JIRA_CLOUD_ID.",
            file=sys.stderr,
        )
        sys.exit(1)


def _last_changelog_transition(
    issue: dict[str, Any],
    field: str,
) -> tuple[str, str]:
    """Return (fromString, toString) for the chronologically last item for `field`."""
    last_from = ""
    last_to = ""
    changelog = issue.get("changelog") or {}
    for history in changelog.get("histories") or []:
        for item in history.get("items") or []:
            if item.get("field") == field:
                last_from = str(item.get("fromString") or "")
                last_to = str(item.get("toString") or "")
    return (last_from, last_to)


def _changelog_has_field(issue: dict[str, Any], field: str) -> bool:
    changelog = issue.get("changelog") or {}
    for history in changelog.get("histories") or []:
        for item in history.get("items") or []:
            if item.get("field") == field:
                return True
    return False


def _changelog_touches_priority_or_issuetype(issue: dict[str, Any]) -> bool:
    return _changelog_has_field(issue, "priority") or _changelog_has_field(issue, "issuetype")


def _was_converted_bug_to_story(issue: dict[str, Any]) -> bool:
    changelog = issue.get("changelog") or {}
    for history in changelog.get("histories") or []:
        for item in history.get("items") or []:
            if (
                item.get("field") == "issuetype"
                and item.get("fromString") == "Bug"
                and item.get("toString") == "Story"
            ):
                return True
    return False


def _priority_name(issue: dict[str, Any]) -> str:
    fields = issue.get("fields") or {}
    priority_obj = fields.get("priority") or {}
    return str(priority_obj.get("name") or "").strip()


def _fetch_search_page(
    client: httpx.Client,
    *,
    jira_url: str,
    jql: str,
    page_size: int,
    next_page_token: str | None,
    fields: str,
    pacer: MinIntervalPacer | None,
) -> tuple[list[dict[str, Any]], str | None]:
    if pacer is not None:
        pacer.before_request()
    try:
        params: dict[str, str | int] = {
            "jql": jql,
            "maxResults": page_size,
            "expand": "changelog",
            "fields": fields,
        }
        if next_page_token:
            params["nextPageToken"] = next_page_token
        resp = client.get(f"{jira_url}/rest/api/3/search/jql", params=params)
        resp.raise_for_status()
        data = resp.json()
        issues_raw = data.get("issues")
        issues: list[dict[str, Any]] = issues_raw if isinstance(issues_raw, list) else []
        token = data.get("nextPageToken")
        next_out: str | None = token if isinstance(token, str) and token else None
        return issues, next_out
    finally:
        if pacer is not None:
            pacer.after_response()


def _try_append_misprioritized(issue: dict[str, Any], got: list[dict[str, str]]) -> bool:
    p_from, p_to = _last_changelog_transition(issue, "priority")
    if not p_to:
        return False
    current = _priority_name(issue)
    if current and p_to != current:
        return False
    t_from, t_to = _last_changelog_transition(issue, "issuetype")
    got.append(
        {
            "benchmark_bucket": BUCKET_MISPRIORITIZED,
            "issue_key": str(issue.get("key") or ""),
            "priority_change_from": p_from,
            "priority_change_to": p_to,
            "issue_type_change_from": t_from,
            "issue_type_change_to": t_to,
        },
    )
    return True


def _collect_misprioritized(
    client: httpx.Client,
    *,
    jira_url: str,
    project: str,
    priorities: list[str],
    per_priority: int,
    page_size: int,
    pacer: MinIntervalPacer | None,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    total_priorities = len(priorities)
    for idx, priority in enumerate(priorities, start=1):
        _LOG.info(
            "Misprioritized bugs: priority %s (%d/%d), target %d",
            priority,
            idx,
            total_priorities,
            per_priority,
        )
        jql = (
            f"project = {project} AND issuetype = Bug "
            f"AND statusCategory = Done "
            f"AND priority = {priority} "
            f"AND priority changed to {priority} "
            f"ORDER BY updated DESC"
        )
        next_page_token: str | None = None
        got: list[dict[str, str]] = []
        page_num = 0
        while len(got) < per_priority:
            page_num += 1
            issues, next_page_token = _fetch_search_page(
                client,
                jira_url=jira_url,
                jql=jql,
                page_size=page_size,
                next_page_token=next_page_token,
                fields="key,priority",
                pacer=pacer,
            )
            _LOG.info(
                "  %s: search page %d → %d issues (accepted %d/%d)",
                priority,
                page_num,
                len(issues),
                len(got),
                per_priority,
            )
            if not issues:
                break
            for issue in issues:
                if _try_append_misprioritized(issue, got) and len(got) >= per_priority:
                    break
            if len(got) >= per_priority:
                break
            if not next_page_token:
                break
        _LOG.info("  %s: finished with %d/%d rows", priority, len(got), per_priority)
        rows.extend(got)
    return rows


def _collect_stories_from_bugs(
    client: httpx.Client,
    *,
    jira_url: str,
    project: str,
    target: int,
    page_size: int,
    pacer: MinIntervalPacer | None,
) -> list[dict[str, str]]:
    _LOG.info("Stories from bugs: target %d", target)
    jql = f"project = {project} AND issuetype = Story ORDER BY created DESC"
    rows: list[dict[str, str]] = []
    next_page_token: str | None = None
    page_num = 0
    while len(rows) < target:
        page_num += 1
        issues, next_page_token = _fetch_search_page(
            client,
            jira_url=jira_url,
            jql=jql,
            page_size=page_size,
            next_page_token=next_page_token,
            fields="key",
            pacer=pacer,
        )
        _LOG.info(
            "  page %d → %d issues (accepted %d/%d)",
            page_num,
            len(issues),
            len(rows),
            target,
        )
        if not issues:
            break
        for issue in issues:
            if not _was_converted_bug_to_story(issue):
                continue
            p_from, p_to = _last_changelog_transition(issue, "priority")
            t_from, t_to = _last_changelog_transition(issue, "issuetype")
            rows.append(
                {
                    "benchmark_bucket": BUCKET_STORY_FROM_BUG,
                    "issue_key": str(issue.get("key") or ""),
                    "priority_change_from": p_from,
                    "priority_change_to": p_to,
                    "issue_type_change_from": t_from,
                    "issue_type_change_to": t_to,
                },
            )
            if len(rows) >= target:
                break
        if len(rows) >= target:
            break
        if not next_page_token:
            break
    _LOG.info("Stories from bugs: finished with %d/%d rows", len(rows), target)
    return rows


def _collect_stable_bugs(
    client: httpx.Client,
    *,
    jira_url: str,
    project: str,
    target: int,
    page_size: int,
    pacer: MinIntervalPacer | None,
) -> list[dict[str, str]]:
    _LOG.info("Stable bugs (no priority/type changelog): target %d", target)
    jql = f"project = {project} AND issuetype = Bug ORDER BY created DESC"
    rows: list[dict[str, str]] = []
    next_page_token: str | None = None
    page_num = 0
    while len(rows) < target:
        page_num += 1
        issues, next_page_token = _fetch_search_page(
            client,
            jira_url=jira_url,
            jql=jql,
            page_size=page_size,
            next_page_token=next_page_token,
            fields="key",
            pacer=pacer,
        )
        _LOG.info(
            "  page %d → %d issues (accepted %d/%d)",
            page_num,
            len(issues),
            len(rows),
            target,
        )
        if not issues:
            break
        for issue in issues:
            if _changelog_touches_priority_or_issuetype(issue):
                continue
            rows.append(
                {
                    "benchmark_bucket": BUCKET_STABLE_BUG,
                    "issue_key": str(issue.get("key") or ""),
                    "priority_change_from": "",
                    "priority_change_to": "",
                    "issue_type_change_from": "",
                    "issue_type_change_to": "",
                },
            )
            if len(rows) >= target:
                break
        if len(rows) >= target:
            break
        if not next_page_token:
            break
    _LOG.info("Stable bugs: finished with %d/%d rows", len(rows), target)
    return rows


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog="Jira requests are spaced by default to avoid hammering the API; tune with "
        "--request-interval.",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="benchmark_dataset.csv",
        help="Output CSV path (default: benchmark_dataset.csv)",
    )
    parser.add_argument("--project", default="BC", help="Jira project key (default: BC)")
    parser.add_argument(
        "--per-priority",
        type=int,
        default=10,
        help="Target misprioritized bugs per final priority (default: 10)",
    )
    parser.add_argument(
        "--priorities",
        default="P0,P1,P2,P3,P4",
        help="Comma-separated final priorities for misprioritized bucket (default: P0-P4)",
    )
    parser.add_argument(
        "--stories-from-bugs",
        type=int,
        default=50,
        help="Target Story count (Bug→Story in changelog) (default: 50)",
    )
    parser.add_argument(
        "--stable-bugs",
        type=int,
        default=50,
        help="Target bugs with no priority/issuetype changelog (default: 50)",
    )
    parser.add_argument("--page-size", type=int, default=100, help="Search page size")
    parser.add_argument(
        "--request-interval",
        type=float,
        default=0.45,
        metavar="SEC",
        help="Minimum seconds between consecutive Jira HTTP completions (default: 0.45). "
        "Use 0 to disable pacing.",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Only errors and the final summary line",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Debug logging (includes JQL snippets)",
    )
    return parser.parse_args()


def _configure_logging(*, quiet: bool, verbose: bool) -> None:
    if quiet:
        level = logging.WARNING
    elif verbose:
        level = logging.DEBUG
    else:
        level = logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )


def main() -> None:
    _require_credentials()
    assert EMAIL is not None and API_TOKEN is not None
    args = _parse_args()
    _configure_logging(quiet=args.quiet, verbose=args.verbose)
    priorities = [p.strip() for p in str(args.priorities).split(",") if p.strip()]
    project = str(args.project).strip()

    pacer: MinIntervalPacer | None
    if args.request_interval and args.request_interval > 0:
        pacer = MinIntervalPacer(args.request_interval)
        _LOG.info("Pacing: at least %.2fs between Jira requests", args.request_interval)
    else:
        pacer = None
        _LOG.warning("Request pacing disabled (--request-interval 0)")

    all_rows: list[dict[str, str]] = []
    with httpx.Client(auth=(EMAIL, API_TOKEN), timeout=60.0) as client:
        if args.verbose:
            _LOG.debug("Base URL: %s", JIRA_URL)
        _LOG.info("--- Phase 1/3: misprioritized bugs ---")
        all_rows.extend(
            _collect_misprioritized(
                client,
                jira_url=JIRA_URL,
                project=project,
                priorities=priorities,
                per_priority=args.per_priority,
                page_size=min(50, args.page_size),
                pacer=pacer,
            ),
        )
        _LOG.info("--- Phase 2/3: stories from bugs ---")
        all_rows.extend(
            _collect_stories_from_bugs(
                client,
                jira_url=JIRA_URL,
                project=project,
                target=args.stories_from_bugs,
                page_size=args.page_size,
                pacer=pacer,
            ),
        )
        _LOG.info("--- Phase 3/3: stable bugs ---")
        all_rows.extend(
            _collect_stable_bugs(
                client,
                jira_url=JIRA_URL,
                project=project,
                target=args.stable_bugs,
                page_size=args.page_size,
                pacer=pacer,
            ),
        )

    out_path = args.output
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(CSV_COLUMNS))
        writer.writeheader()
        for row in all_rows:
            writer.writerow({k: row.get(k, "") for k in CSV_COLUMNS})

    msg = f"Wrote {len(all_rows)} rows to {out_path}"
    _LOG.info(msg)
    if args.quiet:
        print(msg)


if __name__ == "__main__":
    main()
