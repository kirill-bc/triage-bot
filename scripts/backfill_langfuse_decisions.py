#!/usr/bin/env python3
"""Export triage decision records from Langfuse traces to JSON.

This is a one-shot backfill helper for analytics bootstrap. It reads Langfuse
traces/observations and emits rows shaped for decision-event ingestion.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
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


def _iter_paginated(
    client: httpx.Client,
    path: str,
    *,
    base_params: dict[str, Any] | None = None,
    page_size: int = 100,
) -> list[dict[str, Any]]:
    params = dict(base_params or {})
    page = 1
    rows: list[dict[str, Any]] = []
    while True:
        req_params = {**params, "page": page, "limit": page_size}
        response = client.get(path, params=req_params, timeout=30.0)
        response.raise_for_status()
        payload = cast(dict[str, Any], response.json())
        data = payload.get("data")
        if not isinstance(data, list):
            break
        batch = [item for item in data if isinstance(item, dict)]
        if not batch:
            break
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
        "occurred_at": _trace_timestamp(trace),
    }

    session_cost = _sum_session_cost(observations)
    if session_cost is not None:
        row["inference_cost_usd"] = session_cost
    return row, "ok"


def _sort_key_latest(row: dict[str, Any]) -> tuple[str, str]:
    issue_key = str(row.get("issue_key") or "")
    ts = str(row.get("occurred_at") or "")
    return (issue_key, ts)


def latest_per_issue(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return []
    chosen: dict[str, dict[str, Any]] = {}
    for row in sorted(rows, key=_sort_key_latest):
        issue_key = str(row.get("issue_key") or "")
        if issue_key:
            chosen[issue_key] = row
    return list(chosen.values())


def _collect_trace_ids_by_observation_name(
    client: httpx.Client,
    *,
    observation_name: str,
    page_size: int,
) -> list[str]:
    observations = _iter_paginated(
        client,
        "/api/public/observations",
        base_params={"name": observation_name},
        page_size=page_size,
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
) -> list[dict[str, Any]]:
    creds = _read_langfuse_credentials()
    auth = (creds.public_key, creds.secret_key)
    with httpx.Client(base_url=creds.base_url, auth=auth) as client:
        trace_ids = _collect_trace_ids_by_observation_name(
            client,
            observation_name=trace_name,
            page_size=page_size,
        )
        processing_trace_ids = trace_ids
        if max_records is not None:
            processing_trace_ids = trace_ids[:max_records]
        rows: list[dict[str, Any]] = []
        iterator = tqdm(processing_trace_ids, desc="Exporting traces", unit="trace", disable=False)
        for trace_id in iterator:
            trace_response = client.get(f"/api/public/traces/{trace_id}", timeout=30.0)
            trace_response.raise_for_status()
            trace = cast(dict[str, Any], trace_response.json())
            if not isinstance(trace, dict):
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
            if row is not None:
                rows.append(row)
                continue
    if latest_only:
        return latest_per_issue(rows)
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill triage decision records from Langfuse and write JSON rows for DB ingestion."
        ),
    )
    parser.add_argument(
        "-o",
        "--output",
        default=str(_ROOT / "backfill_langfuse_decisions.json"),
        help="Path to output JSON file.",
    )
    parser.add_argument(
        "--env-file",
        default=str(_ROOT / ".env"),
        help="Optional dotenv file loaded before reading LANGFUSE_* credentials.",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=100,
        help="Page size for Langfuse API pagination (default: 100).",
    )
    parser.add_argument(
        "--latest-per-issue",
        action="store_true",
        help="Keep only the latest session per issue_key in output.",
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
        help="Langfuse trace name filter for /traces query (default: triage_issue_pipeline).",
    )
    parser.add_argument(
        "--max-records",
        type=int,
        default=None,
        help="Optional cap for number of output rows written to JSON.",
    )
    ns = parser.parse_args(argv)

    env_path = Path(ns.env_file)
    if env_path.is_file():
        load_dotenv(env_path, override=False)

    try:
        rows = export_backfill(
            page_size=ns.page_size,
            latest_only=bool(ns.latest_per_issue),
            default_source=ns.default_source,
            trace_name=str(ns.trace_name),
            max_records=ns.max_records,
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


if __name__ == "__main__":
    raise SystemExit(main())
