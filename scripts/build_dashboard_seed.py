#!/usr/bin/env python3
"""Build dashboard decision seed JSON from Langfuse traces and Jira metadata.

Subcommands::

    backfill  — one-shot export of historical decision rows from Langfuse traces.
    catchup   — incremental export since the committed seed file (idempotent).
    enrich    — add issue_created_at / issue_name to an existing JSON file via Jira.

Run ``python scripts/build_dashboard_seed.py <subcommand> --help`` for details.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

import httpx
from dotenv import load_dotenv
from tqdm import tqdm

_ROOT = Path(__file__).resolve().parents[1]

_ISSUE_TYPE_RE = re.compile(
    r"(?:current\s+)?(?:jira\s+)?issue\s*type\s*:\s*(Bug|Story)\b",
    re.IGNORECASE,
)
_PRIORITY_RE = re.compile(
    r"(?:current\s+)?(?:jira\s+)?priority\s*:\s*(P[0-4])\b",
    re.IGNORECASE,
)
_PROJECT_ISSUE_KEY_RE = re.compile(r"^[A-Z]+-\d+$")
_DEFAULT_MIN_OCCURRED_AT = "2026-05-25"
_DEFAULT_EXCLUDE_ISSUES = "BC-22932"
_LANGFUSE_GET_RETRIES = 3
_LANGFUSE_RETRYABLE_STATUSES = frozenset({429, 502, 503, 504})


def _row_triaged_at_iso(row: dict[str, Any]) -> str:
    """Return triage timestamp from a row (``triaged_at`` or legacy ``occurred_at``)."""
    return str(row.get("triaged_at") or row.get("occurred_at") or "")


def align_decision_row_fields(row: dict[str, Any]) -> dict[str, Any]:
    """Normalize export rows to dashboard ``DecisionEvent`` keys."""
    out = dict(row)
    if not str(out.get("triaged_at") or "").strip() and out.get("occurred_at"):
        out["triaged_at"] = out.pop("occurred_at")
    else:
        out.pop("occurred_at", None)
    return out


def _parse_occurred_at_bound(raw: str) -> datetime:
    """Parse ``YYYY-MM-DD`` or ISO-8601 into UTC ``datetime``."""
    text = raw.strip()
    if not text:
        msg = "min-occurred-at value is empty."
        raise ValueError(msg)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        text = f"{text}T00:00:00Z"
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    if text.endswith("+0000"):
        text = f"{text[:-5]}+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _observation_start_time(observation: dict[str, Any]) -> datetime | None:
    for key in ("startTime", "start_time"):
        raw = observation.get(key)
        if isinstance(raw, str) and raw.strip():
            return _parse_occurred_at_bound(raw)
    return None


def _trace_occurred_at_datetime(trace: dict[str, Any]) -> datetime:
    return _parse_occurred_at_bound(_trace_timestamp(trace))


def _ensure_triage_import_path() -> None:
    src = _ROOT / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))


def parse_project_filters(project_filter: str) -> frozenset[str]:
    """Parse comma-separated Jira project keys (e.g. ``BC,TJC``); empty means no filter."""
    return frozenset(
        part.strip().upper()
        for part in project_filter.split(",")
        if part.strip()
    )


def row_matches_project_filter(row: dict[str, Any], project_filter: str) -> bool:
    """Return True when row project and issue_key match any filter (e.g. BC / BC-123)."""
    wanted = parse_project_filters(project_filter)
    if not wanted:
        return True
    project = str(row.get("project") or "").strip().upper()
    issue_key = str(row.get("issue_key") or "").strip().upper()
    if project not in wanted:
        return False
    return bool(_PROJECT_ISSUE_KEY_RE.match(issue_key) and issue_key.startswith(f"{project}-"))


@dataclass(frozen=True)
class LangfuseCredentials:
    public_key: str
    secret_key: str
    base_url: str


def _read_langfuse_credentials() -> LangfuseCredentials:
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY", "").strip()
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY", "").strip()
    base_url = (
        os.environ.get("LANGFUSE_BASE_URL", "").strip()
        or os.environ.get("LANGFUSE_HOST", "").strip()
    )
    if not public_key or not secret_key:
        msg = "Missing Langfuse credentials: set LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY."
        raise ValueError(msg)
    if not base_url:
        msg = "Missing Langfuse host: set LANGFUSE_BASE_URL (or LANGFUSE_HOST)."
        raise ValueError(msg)
    return LangfuseCredentials(
        public_key=public_key,
        secret_key=secret_key,
        base_url=base_url.rstrip("/"),
    )


def _langfuse_get(
    client: httpx.Client,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> httpx.Response:
    """GET with short retries for transient Langfuse gateway errors."""
    last_response: httpx.Response | None = None
    for attempt in range(_LANGFUSE_GET_RETRIES):
        response = client.get(path, params=params, timeout=timeout)
        if response.status_code not in _LANGFUSE_RETRYABLE_STATUSES:
            response.raise_for_status()
            return response
        last_response = response
        if attempt + 1 < _LANGFUSE_GET_RETRIES:
            time.sleep(1.5 * (attempt + 1))
    if last_response is not None:
        last_response.raise_for_status()
    msg = f"Langfuse GET failed for {path}"
    raise RuntimeError(msg)


def _iter_paginated(
    client: httpx.Client,
    path: str,
    *,
    base_params: dict[str, Any] | None = None,
    page_size: int = 100,
    stop_on_start_time_before: datetime | None = None,
) -> list[dict[str, Any]]:
    params = dict(base_params or {})
    page = 1
    rows: list[dict[str, Any]] = []
    while True:
        req_params = {**params, "page": page, "limit": page_size}
        response = _langfuse_get(client, path, params=req_params)
        payload = cast(dict[str, Any], response.json())
        data = payload.get("data")
        if not isinstance(data, list):
            break
        batch = [item for item in data if isinstance(item, dict)]
        if not batch:
            break
        if stop_on_start_time_before is not None:
            hit_cutoff = False
            for item in batch:
                start = _observation_start_time(item)
                if start is not None and start < stop_on_start_time_before:
                    hit_cutoff = True
                    break
                rows.append(item)
            if hit_cutoff:
                break
        else:
            rows.extend(batch)
        meta = payload.get("meta")
        if len(batch) < page_size:
            break
        if isinstance(meta, dict):
            total_pages = meta.get("totalPages")
            if isinstance(total_pages, int) and page >= total_pages:
                break
            next_page = meta.get("nextPage")
            if next_page is None:
                page += 1
                continue
            if isinstance(next_page, int):
                page = next_page
                continue
        page += 1
    return rows


def _observation_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        pieces = [_observation_text(v) for v in value.values()]
        return "\n".join(piece for piece in pieces if piece)
    if isinstance(value, list):
        pieces = [_observation_text(item) for item in value]
        return "\n".join(piece for piece in pieces if piece)
    return ""


def _extract_intake_from_prompt(input_payload: Any) -> tuple[str | None, str | None]:
    text = _observation_text(input_payload)
    issue_match = _ISSUE_TYPE_RE.search(text)
    pri_match = _PRIORITY_RE.search(text)
    issue_type = issue_match.group(1).title() if issue_match else None
    priority = pri_match.group(1).upper() if pri_match else None
    return issue_type, priority


def _extract_parsed_payload(observation: dict[str, Any]) -> dict[str, Any]:
    metadata = observation.get("metadata")
    if isinstance(metadata, dict):
        parsed = metadata.get("parsed")
        if isinstance(parsed, dict):
            return parsed
    return {}


def _as_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _extract_observation_cost(observation: dict[str, Any]) -> float | None:
    top_level_cost = (
        _as_float(observation.get("totalCost"))
        or _as_float(observation.get("total_cost"))
        or _as_float(observation.get("cost"))
    )
    if top_level_cost is not None:
        return top_level_cost
    details = observation.get("costDetails")
    if not isinstance(details, dict):
        details = observation.get("cost_details")
    if not isinstance(details, dict):
        return None
    preferred = (
        _as_float(details.get("total"))
        or _as_float(details.get("total_cost"))
        or _as_float(details.get("totalCost"))
        or _as_float(details.get("cost"))
    )
    if preferred is not None:
        return preferred
    parts = [_as_float(v) for v in details.values()]
    numeric_parts = [p for p in parts if p is not None]
    if not numeric_parts:
        return None
    return float(sum(numeric_parts))


def _sum_session_cost(observations: list[dict[str, Any]]) -> float | None:
    costs = [_extract_observation_cost(obs) for obs in observations]
    numeric = [c for c in costs if c is not None]
    if not numeric:
        return None
    return float(sum(numeric))


def _find_observation(
    observations: list[dict[str, Any]],
    *,
    name: str,
) -> dict[str, Any] | None:
    matches: list[dict[str, Any]] = []
    for obs in observations:
        if str(obs.get("name") or "").strip() == name:
            matches.append(obs)
    if not matches:
        return None
    latest = sorted(
        matches,
        key=lambda item: str(item.get("startTime") or item.get("start_time") or ""),
    )[-1]
    return latest


def _trace_timestamp(trace: dict[str, Any]) -> str:
    for key in ("timestamp", "startTime", "start_time", "createdAt", "created_at"):
        value = trace.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return datetime.now(timezone.utc).isoformat()


def _extract_issue_key(trace: dict[str, Any]) -> str | None:
    metadata = trace.get("metadata")
    if isinstance(metadata, dict):
        issue_key = metadata.get("issue_key")
        if isinstance(issue_key, str) and issue_key.strip():
            return issue_key.strip()
    return None


def _extract_project(trace: dict[str, Any]) -> str | None:
    metadata = trace.get("metadata")
    if isinstance(metadata, dict):
        project = metadata.get("project")
        if isinstance(project, str) and project.strip():
            return project.strip()
    return None


def _extract_run_id(trace: dict[str, Any]) -> str | None:
    metadata = trace.get("metadata")
    if isinstance(metadata, dict):
        run_id = metadata.get("run_id")
        if isinstance(run_id, str) and run_id.strip():
            return run_id.strip()
    session_id = trace.get("sessionId") or trace.get("session_id")
    if isinstance(session_id, str) and session_id.strip():
        return session_id.strip()
    return None


def _extract_session_id(trace: dict[str, Any], run_id: str) -> str:
    session_id = trace.get("sessionId") or trace.get("session_id")
    if isinstance(session_id, str) and session_id.strip():
        return session_id.strip()
    return run_id


def build_decision_row(
    trace: dict[str, Any],
    observations: list[dict[str, Any]],
    *,
    default_source: str,
) -> tuple[dict[str, Any] | None, str]:
    run_id = _extract_run_id(trace)
    issue_key = _extract_issue_key(trace)
    project = _extract_project(trace)
    if run_id is None or issue_key is None or project is None:
        return None, "missing_trace_metadata"

    cls_obs = _find_observation(observations, name="inference_classification")
    if cls_obs is None:
        return None, "missing_classification_observation"
    pri_obs = _find_observation(observations, name="inference_priority")

    cls_parsed = _extract_parsed_payload(cls_obs)
    pri_parsed = _extract_parsed_payload(pri_obs) if pri_obs is not None else {}

    recommended_issue_type = cls_parsed.get("recommended_issue_type")
    if not isinstance(recommended_issue_type, str):
        return None, "missing_recommended_issue_type"
    recommended_issue_type = recommended_issue_type.title()
    if recommended_issue_type not in {"Bug", "Story"}:
        return None, "invalid_recommended_issue_type"

    recommended_priority: str | None = None
    if recommended_issue_type == "Bug":
        pri = pri_parsed.get("recommended_priority")
        if isinstance(pri, str) and pri.strip():
            recommended_priority = pri.strip().upper()

    confidence = _as_float(cls_parsed.get("confidence"))
    if confidence is None:
        confidence = _as_float(pri_parsed.get("confidence"))
    reason_raw = cls_parsed.get("reason")
    if not isinstance(reason_raw, str) or not reason_raw.strip():
        reason_raw = pri_parsed.get("reason") if isinstance(pri_parsed.get("reason"), str) else ""
    reason = str(reason_raw).strip()

    intake_issue_type, intake_priority = _extract_intake_from_prompt(cls_obs.get("input"))
    if intake_issue_type == "Story":
        intake_priority = None

    row: dict[str, Any] = {
        "event_type": "triage_completed",
        "run_id": run_id,
        "session_id": _extract_session_id(trace, run_id),
        "issue_key": issue_key,
        "project": project,
        "source": default_source,
        "intake_issue_type": intake_issue_type,
        "intake_priority": intake_priority,
        "recommended_issue_type": recommended_issue_type,
        "recommended_priority": recommended_priority,
        "applied_type_change": False,
        "applied_priority_change": False,
        "confidence": confidence,
        "reason": reason,
        "triaged_at": _trace_timestamp(trace),
    }

    session_cost = _sum_session_cost(observations)
    if session_cost is not None:
        row["inference_cost_usd"] = session_cost
    return row, "ok"


def _sort_key_latest(row: dict[str, Any]) -> tuple[str, str]:
    issue_key = str(row.get("issue_key") or "")
    return (issue_key, _row_triaged_at_iso(row))


def _jira_metadata_fully_cached(row: dict[str, Any], *, skip_existing: bool) -> bool:
    if not skip_existing:
        return False
    created = row.get("issue_created_at")
    name = row.get("issue_name")
    has_created = isinstance(created, str) and bool(created.strip())
    has_name = isinstance(name, str) and bool(name.strip())
    return has_created and has_name


def enrich_rows_with_issue_created_at(
    rows: list[dict[str, Any]],
    *,
    env_file: Path | None = None,
    skip_existing: bool = False,
) -> list[dict[str, Any]]:
    """Attach ``issue_created_at`` and ``issue_name`` from Jira (cached per issue_key)."""
    if not rows:
        return []
    _ensure_triage_import_path()
    if env_file is not None and env_file.is_file():
        load_dotenv(env_file, override=False)
    from triage_service.adapters.jira_issue_fetcher import (  # noqa: PLC0415
        IssueDecisionMetadata,
        JiraIssueFetchError,
        JiraIssueFetcher,
    )
    from triage_service.core.settings import load_settings  # noqa: PLC0415

    settings = load_settings()
    fetcher = JiraIssueFetcher(settings)
    cache: dict[str, IssueDecisionMetadata] = {}
    unique_keys = sorted({str(row["issue_key"]) for row in rows if row.get("issue_key")})
    keys_to_fetch: list[str] = []
    for key in unique_keys:
        if skip_existing and all(
            _jira_metadata_fully_cached(row, skip_existing=True)
            for row in rows
            if str(row.get("issue_key") or "") == key
        ):
            continue
        keys_to_fetch.append(key)
    for issue_key in tqdm(keys_to_fetch, desc="Fetching Jira metadata", unit="issue", disable=False):
        try:
            cache[issue_key] = fetcher.fetch_decision_metadata(issue_key, run_id="backfill")
        except JiraIssueFetchError as exc:
            print(
                f"Warning: could not fetch Jira metadata for {issue_key}: {exc}",
                file=sys.stderr,
            )
            cache[issue_key] = IssueDecisionMetadata()
    enriched: list[dict[str, Any]] = []
    for row in rows:
        issue_key = str(row.get("issue_key") or "")
        meta = cache.get(issue_key)
        merged = dict(row)
        if meta is not None:
            if meta.issue_created_at and (not skip_existing or not merged.get("issue_created_at")):
                merged["issue_created_at"] = meta.issue_created_at
            if meta.issue_name and (not skip_existing or not merged.get("issue_name")):
                merged["issue_name"] = meta.issue_name
        enriched.append(align_decision_row_fields(merged))
    return enriched


def latest_per_issue(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return []
    chosen: dict[str, dict[str, Any]] = {}
    for row in sorted(rows, key=_sort_key_latest):
        issue_key = str(row.get("issue_key") or "")
        if issue_key:
            chosen[issue_key] = row
    return list(chosen.values())


def load_decision_rows_from_json(path: Path) -> list[dict[str, Any]]:
    """Load decision rows from a JSON array file; missing path yields an empty list."""
    if not path.is_file():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        msg = f"Seed file must be a JSON array: {path}"
        raise ValueError(msg)
    return [cast(dict[str, Any], row) for row in raw if isinstance(row, dict)]


def latest_triaged_at(rows: list[dict[str, Any]]) -> datetime | None:
    """Return the latest ``triaged_at`` (or legacy ``occurred_at``) across rows."""
    latest: datetime | None = None
    for row in rows:
        iso = _row_triaged_at_iso(row).strip()
        if not iso:
            continue
        try:
            dt = _parse_occurred_at_bound(iso)
        except ValueError:
            continue
        if latest is None or dt > latest:
            latest = dt
    return latest


def collect_run_ids(rows: list[dict[str, Any]]) -> set[str]:
    """Collect non-empty ``run_id`` values from decision rows."""
    ids: set[str] = set()
    for row in rows:
        run_id = row.get("run_id")
        if isinstance(run_id, str) and run_id.strip():
            ids.add(run_id.strip())
    return ids


def exclude_rows_with_run_ids(
    rows: list[dict[str, Any]],
    *,
    known_run_ids: set[str],
) -> list[dict[str, Any]]:
    """Drop rows whose ``run_id`` is already present in ``known_run_ids``."""
    if not known_run_ids:
        return list(rows)
    kept: list[dict[str, Any]] = []
    for row in rows:
        run_id = row.get("run_id")
        if isinstance(run_id, str) and run_id.strip() in known_run_ids:
            continue
        kept.append(row)
    return kept


def parse_excluded_issues(raw: str) -> frozenset[str]:
    """Parse comma-separated issue keys into a frozen set (uppercased)."""
    return frozenset(
        part.strip().upper()
        for part in raw.split(",")
        if part.strip()
    )


def exclude_blacklisted_issues(
    rows: list[dict[str, Any]],
    *,
    excluded_issues: frozenset[str],
) -> list[dict[str, Any]]:
    """Drop rows whose ``issue_key`` is in the blacklist."""
    if not excluded_issues:
        return list(rows)
    return [
        row for row in rows
        if str(row.get("issue_key") or "").strip().upper() not in excluded_issues
    ]


def export_catchup(
    *,
    seed_path: Path,
    page_size: int,
    default_source: str,
    trace_name: str,
    project_filter: str = "BC",
    excluded_issues: frozenset[str] = frozenset(),
    enrich_jira_created: bool = True,
    env_file: Path | None = None,
    max_records: int | None = None,
) -> list[dict[str, Any]]:
    """Export Langfuse decision rows newer than the seed file, excluding known ``run_id``s."""
    seed_rows = load_decision_rows_from_json(seed_path)
    known_run_ids = collect_run_ids(seed_rows)
    min_occurred_at = latest_triaged_at(seed_rows)
    if min_occurred_at is None:
        min_occurred_at = _parse_occurred_at_bound(_DEFAULT_MIN_OCCURRED_AT)

    rows = export_backfill(
        page_size=page_size,
        latest_only=False,
        default_source=default_source,
        trace_name=trace_name,
        max_records=max_records,
        project_filter=project_filter,
        excluded_issues=excluded_issues,
        enrich_jira_created=enrich_jira_created,
        env_file=env_file,
        min_occurred_at=min_occurred_at,
    )
    return exclude_rows_with_run_ids(rows, known_run_ids=known_run_ids)


def _collect_trace_ids_by_observation_name(
    client: httpx.Client,
    *,
    observation_name: str,
    page_size: int,
    min_occurred_at: datetime | None = None,
) -> list[str]:
    # Client-side cutoff only: Langfuse v1 observations + fromStartTime 502s on US cloud.
    observations = _iter_paginated(
        client,
        "/api/public/observations",
        base_params={"name": observation_name},
        page_size=page_size,
        stop_on_start_time_before=min_occurred_at,
    )
    seen: set[str] = set()
    trace_ids: list[str] = []
    for obs in observations:
        trace_id_raw = obs.get("traceId") or obs.get("trace_id")
        if not isinstance(trace_id_raw, str):
            continue
        trace_id = trace_id_raw.strip()
        if not trace_id or trace_id in seen:
            continue
        seen.add(trace_id)
        trace_ids.append(trace_id)
    return trace_ids


def export_backfill(
    *,
    page_size: int,
    latest_only: bool,
    default_source: str,
    trace_name: str,
    max_records: int | None,
    project_filter: str = "BC",
    excluded_issues: frozenset[str] = frozenset(),
    enrich_jira_created: bool = True,
    env_file: Path | None = None,
    min_occurred_at: datetime | None = None,
) -> list[dict[str, Any]]:
    creds = _read_langfuse_credentials()
    auth = (creds.public_key, creds.secret_key)
    with httpx.Client(base_url=creds.base_url, auth=auth) as client:
        trace_ids = _collect_trace_ids_by_observation_name(
            client,
            observation_name=trace_name,
            page_size=page_size,
            min_occurred_at=min_occurred_at,
        )
        processing_trace_ids = trace_ids
        if max_records is not None:
            processing_trace_ids = trace_ids[:max_records]
        rows: list[dict[str, Any]] = []
        iterator = tqdm(processing_trace_ids, desc="Exporting traces", unit="trace", disable=False)
        for trace_id in iterator:
            trace_response = _langfuse_get(client, f"/api/public/traces/{trace_id}")
            trace = cast(dict[str, Any], trace_response.json())
            if not isinstance(trace, dict):
                continue
            if min_occurred_at is not None and _trace_occurred_at_datetime(trace) < min_occurred_at:
                continue
            observations = _iter_paginated(
                client,
                "/api/public/observations",
                base_params={"traceId": trace_id},
                page_size=page_size,
            )
            row, _ = build_decision_row(
                trace,
                observations,
                default_source=default_source,
            )
            if row is None or not row_matches_project_filter(row, project_filter):
                continue
            if excluded_issues:
                issue_key = str(row.get("issue_key") or "").strip().upper()
                if issue_key in excluded_issues:
                    continue
            if min_occurred_at is not None:
                triaged = _parse_occurred_at_bound(_row_triaged_at_iso(row))
                if triaged < min_occurred_at:
                    continue
            rows.append(align_decision_row_fields(row))
    if enrich_jira_created:
        rows = enrich_rows_with_issue_created_at(rows, env_file=env_file)
    if latest_only:
        return latest_per_issue(rows)
    return rows


def enrich_decision_file(
    input_path: Path,
    *,
    output_path: Path,
    env_file: Path | None,
    skip_existing: bool,
) -> list[dict[str, Any]]:
    """Load, enrich with Jira created timestamps and issue names, and write rows."""
    rows = load_decision_rows_from_json(input_path)
    if not rows:
        msg = f"Seed file must be a JSON array: {input_path}"
        raise ValueError(msg)
    enriched = enrich_rows_with_issue_created_at(
        rows,
        env_file=env_file,
        skip_existing=skip_existing,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(enriched, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return enriched


def _add_common_langfuse_args(
    parser: argparse.ArgumentParser,
    *,
    exclude_issues_default: str = _DEFAULT_EXCLUDE_ISSUES,
) -> None:
    """Add flags shared by ``backfill`` and ``catchup`` subcommands."""
    parser.add_argument(
        "--page-size",
        type=int,
        default=100,
        help="Page size for Langfuse API pagination (default: 100).",
    )
    parser.add_argument(
        "--default-source",
        default="bug_created",
        choices=["bug_created", "priority_changed", "manual_trigger"],
        help="Fallback source for exported rows (default: bug_created).",
    )
    parser.add_argument(
        "--trace-name",
        default="triage_issue_pipeline",
        help="Langfuse observation name used to discover traces (default: triage_issue_pipeline).",
    )
    parser.add_argument(
        "--max-records",
        type=int,
        default=None,
        help="Optional cap for number of Langfuse traces processed.",
    )
    parser.add_argument(
        "--project-filter",
        "--projects",
        dest="project_filter",
        default="BC",
        metavar="KEYS",
        help=(
            "Keep rows only for these Jira project keys, comma-separated "
            "(e.g. BC,TJC; default: BC). Use an empty value to include all projects."
        ),
    )
    parser.add_argument(
        "--no-jira-created",
        action="store_true",
        help="Skip Jira REST lookup for issue_created_at and issue_name.",
    )
    exclude_help = (
        "Comma-separated issue keys to exclude from export "
        "(e.g. BC-22932,BC-12345). Case-insensitive."
    )
    if exclude_issues_default:
        exclude_help += f" Default: {exclude_issues_default}."
    parser.add_argument(
        "--exclude-issues",
        default=exclude_issues_default,
        metavar="KEYS",
        help=exclude_help,
    )


def _run_backfill(ns: argparse.Namespace) -> int:
    min_occurred_at: datetime | None = None
    if not ns.no_min_occurred_at:
        try:
            min_occurred_at = _parse_occurred_at_bound(str(ns.min_occurred_at))
        except ValueError as exc:
            print(f"Invalid --min-occurred-at: {exc}", file=sys.stderr)
            return 2

    try:
        rows = export_backfill(
            page_size=ns.page_size,
            latest_only=bool(ns.latest_per_issue),
            default_source=ns.default_source,
            trace_name=str(ns.trace_name),
            max_records=ns.max_records,
            project_filter=str(ns.project_filter),
            excluded_issues=parse_excluded_issues(ns.exclude_issues),
            enrich_jira_created=not bool(ns.no_jira_created),
            env_file=ns._env_path if ns._env_path.is_file() else None,
            min_occurred_at=min_occurred_at,
        )
    except (ValueError, httpx.HTTPError) as exc:
        print(f"Backfill export failed: {exc}", file=sys.stderr)
        return 2

    output_path = Path(ns.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(rows, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {len(rows)} decision row(s) to {output_path}", file=sys.stderr)
    return 0


def _run_catchup(ns: argparse.Namespace) -> int:
    seed_path = Path(ns.seed)
    try:
        rows = export_catchup(
            seed_path=seed_path,
            page_size=ns.page_size,
            default_source=ns.default_source,
            trace_name=str(ns.trace_name),
            max_records=ns.max_records,
            project_filter=str(ns.project_filter),
            excluded_issues=parse_excluded_issues(ns.exclude_issues),
            enrich_jira_created=not bool(ns.no_jira_created),
            env_file=ns._env_path if ns._env_path.is_file() else None,
        )
    except (ValueError, httpx.HTTPError) as exc:
        print(f"Catch-up export failed: {exc}", file=sys.stderr)
        return 2

    output_path = Path(ns.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(rows, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(
        f"Wrote {len(rows)} new decision row(s) to {output_path} "
        f"(seed: {seed_path})",
        file=sys.stderr,
    )
    return 0


def _run_enrich(ns: argparse.Namespace) -> int:
    input_path = Path(ns.input)
    if not input_path.is_file():
        print(f"Input file not found: {input_path}", file=sys.stderr)
        return 2

    output_path = Path(ns.output) if ns.output else input_path
    try:
        enriched = enrich_decision_file(
            input_path,
            output_path=output_path,
            env_file=ns._env_path if ns._env_path.is_file() else None,
            skip_existing=not bool(ns.force_refresh),
        )
    except (ValueError, OSError) as exc:
        print(f"Enrichment failed: {exc}", file=sys.stderr)
        return 2

    with_created = sum(1 for row in enriched if row.get("issue_created_at"))
    with_name = sum(1 for row in enriched if row.get("issue_name"))
    print(
        f"Wrote {len(enriched)} row(s) to {output_path} "
        f"({with_created} with issue_created_at, {with_name} with issue_name)",
        file=sys.stderr,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build dashboard decision seed JSON: backfill, catch-up, and Jira enrichment.",
    )
    parser.add_argument(
        "--env-file",
        default=str(_ROOT / ".env"),
        help="Optional dotenv file for Langfuse / Jira credentials.",
    )
    sub = parser.add_subparsers(dest="subcommand")

    # --- backfill ---
    bf = sub.add_parser(
        "backfill",
        help="One-shot export of historical decision rows from Langfuse traces.",
    )
    bf.add_argument(
        "-o",
        "--output",
        default=str(_ROOT / "dashboard_seed.json"),
        help="Path to output JSON file.",
    )
    bf.add_argument(
        "--latest-per-issue",
        action="store_true",
        help="Keep only the latest session per issue_key in output.",
    )
    bf.add_argument(
        "--min-occurred-at",
        default=_DEFAULT_MIN_OCCURRED_AT,
        help=(
            "Skip Langfuse traces before this UTC date/time "
            f"(default: {_DEFAULT_MIN_OCCURRED_AT}, production go-live)."
        ),
    )
    bf.add_argument(
        "--no-min-occurred-at",
        action="store_true",
        help="Export all Langfuse traces regardless of triage timestamp.",
    )
    _add_common_langfuse_args(bf)

    # --- catchup ---
    cu = sub.add_parser(
        "catchup",
        help="Incremental export since the committed seed file (idempotent on run_id).",
    )
    cu.add_argument(
        "--seed",
        default=str(_ROOT / "decisions.json"),
        help="Committed seed JSON array (default: decisions.json in repo root).",
    )
    cu.add_argument(
        "-o",
        "--output",
        default=str(_ROOT / "catchup.json"),
        help="Path to output JSON for rows not in the seed (default: catchup.json).",
    )
    _add_common_langfuse_args(cu)

    # --- enrich ---
    en = sub.add_parser(
        "enrich",
        help="Add issue_created_at / issue_name to decision JSON via Jira REST.",
    )
    en.add_argument(
        "input",
        nargs="?",
        default=str(_ROOT / "decisions.json"),
        help="Path to input JSON array (default: decisions.json in repo root).",
    )
    en.add_argument(
        "-o",
        "--output",
        default=None,
        help="Output path (default: overwrite input file in place).",
    )
    en.add_argument(
        "--force-refresh",
        action="store_true",
        help="Re-fetch Jira metadata even when issue_created_at or issue_name is already set.",
    )

    ns = parser.parse_args(argv)

    env_path = Path(ns.env_file)
    if env_path.is_file():
        load_dotenv(env_path, override=False)
    ns._env_path = env_path  # noqa: SLF001

    if ns.subcommand == "backfill":
        return _run_backfill(ns)
    if ns.subcommand == "catchup":
        return _run_catchup(ns)
    if ns.subcommand == "enrich":
        return _run_enrich(ns)

    parser.print_help(sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
