"""Unit tests for scripts/build_dashboard_seed.py."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast
from unittest.mock import patch

import httpx
import pytest


@pytest.mark.unit
def test_extract_intake_from_prompt_parses_issue_type_and_priority() -> None:
    from scripts.build_dashboard_seed import _extract_intake_from_prompt

    issue_type, priority = _extract_intake_from_prompt(
        [
            {"role": "system", "content": "Policy"},
            {
                "role": "user",
                "content": "Issue block\nCurrent issue type: Bug\nCurrent priority: P3\nMore text",
            },
        ],
    )
    assert issue_type == "Bug"
    assert priority == "P3"


@pytest.mark.unit
def test_build_decision_row_story_without_priority() -> None:
    from scripts.build_dashboard_seed import build_decision_row

    trace = {
        "id": "trace-1",
        "sessionId": "session-1",
        "timestamp": "2026-06-02T10:00:00Z",
        "metadata": {
            "run_id": "run-1",
            "issue_key": "TJC-101",
            "project": "TJC",
        },
    }
    observations = [
        {
            "name": "inference_classification",
            "input": "Current issue type: Bug\nCurrent priority: P2",
            "metadata": {
                "parsed": {
                    "recommended_issue_type": "Story",
                    "confidence": 0.88,
                    "reason": "Not a defect.",
                },
            },
            "costDetails": {"total": 0.01},
        },
    ]

    row, reason = build_decision_row(trace, observations, default_source="bug_created")
    assert reason == "ok"
    assert row is not None
    assert row["session_id"] == "session-1"
    assert row["issue_key"] == "TJC-101"
    assert row["recommended_issue_type"] == "Story"
    assert row["recommended_priority"] is None
    assert row["intake_issue_type"] == "Bug"
    assert row["intake_priority"] == "P2"
    assert row["inference_cost_usd"] == pytest.approx(0.01)
    assert row["triaged_at"] == "2026-06-02T10:00:00Z"
    assert "occurred_at" not in row


@pytest.mark.unit
def test_build_decision_row_bug_uses_priority_generation_and_sums_session_cost() -> None:
    from scripts.build_dashboard_seed import build_decision_row

    trace = {
        "id": "trace-2",
        "timestamp": "2026-06-02T11:00:00Z",
        "metadata": {
            "run_id": "run-2",
            "issue_key": "TJC-202",
            "project": "TJC",
        },
    }
    observations = [
        {
            "name": "inference_classification",
            "input": "Current issue type: Bug\nCurrent priority: P1",
            "metadata": {
                "parsed": {
                    "recommended_issue_type": "Bug",
                    "confidence": 0.77,
                    "reason": "Bug confirmed.",
                },
            },
            "costDetails": {"input": 0.01, "output": 0.02},
        },
        {
            "name": "inference_priority",
            "metadata": {
                "parsed": {
                    "recommended_priority": "P2",
                    "confidence": 0.63,
                    "reason": "Deescalation",
                },
            },
            "costDetails": {"total": 0.03},
        },
        {
            "name": "inference_zendesk_summary",
            "costDetails": {"total": 0.004},
        },
    ]

    row, reason = build_decision_row(trace, observations, default_source="priority_changed")
    assert reason == "ok"
    assert row is not None
    assert row["session_id"] == "run-2"
    assert row["recommended_issue_type"] == "Bug"
    assert row["recommended_priority"] == "P2"
    assert row["source"] == "priority_changed"
    assert row["inference_cost_usd"] == pytest.approx(0.064)


@pytest.mark.unit
def test_align_decision_row_fields_renames_legacy_occurred_at() -> None:
    from scripts.build_dashboard_seed import align_decision_row_fields

    row = align_decision_row_fields(
        {"issue_key": "TJC-1", "occurred_at": "2026-06-02T10:00:00Z"},
    )
    assert row["triaged_at"] == "2026-06-02T10:00:00Z"
    assert "occurred_at" not in row


@pytest.mark.unit
def test_latest_per_issue_sorts_on_legacy_occurred_at() -> None:
    from scripts.build_dashboard_seed import latest_per_issue

    rows = [
        {"issue_key": "TJC-1", "occurred_at": "2026-05-01T10:00:00Z", "run_id": "a"},
        {"issue_key": "TJC-1", "triaged_at": "2026-05-02T10:00:00Z", "run_id": "b"},
    ]
    deduped = latest_per_issue(rows)
    assert deduped[0]["run_id"] == "b"


@pytest.mark.unit
def test_parse_occurred_at_bound_accepts_date_only() -> None:
    from datetime import datetime, timezone

    from scripts.build_dashboard_seed import _parse_occurred_at_bound

    parsed = _parse_occurred_at_bound("2026-05-25")
    assert parsed == datetime(2026, 5, 25, 0, 0, tzinfo=timezone.utc)


@pytest.mark.unit
def test_iter_paginated_stops_when_observation_is_before_cutoff() -> None:
    from datetime import datetime, timezone

    from scripts.build_dashboard_seed import _iter_paginated

    cutoff = datetime(2026, 5, 25, tzinfo=timezone.utc)
    pages: list[dict[str, object]] = [
        {
            "data": [
                {"traceId": "t-new", "startTime": "2026-05-26T10:00:00Z"},
                {"traceId": "t-old", "startTime": "2026-05-24T10:00:00Z"},
            ],
            "meta": {"totalPages": 2},
        },
        {
            "data": [{"traceId": "t-never", "startTime": "2026-05-27T10:00:00Z"}],
            "meta": {"totalPages": 2},
        },
    ]

    class _FakeClient:
        def get(
            self,
            path: str,
            params: object = None,
            timeout: float = 30.0,
        ) -> _BackfillFakeResponse:
            del path, timeout
            assert isinstance(params, dict)
            page = int(str(params["page"]))
            return _BackfillFakeResponse(pages[page - 1])

    rows = _iter_paginated(
        _FakeClient(),  # type: ignore[arg-type]
        "/api/public/observations",
        base_params={"name": "triage_issue_pipeline"},
        page_size=100,
        stop_on_start_time_before=cutoff,
    )
    assert [row["traceId"] for row in rows] == ["t-new"]


@pytest.mark.unit
def test_langfuse_get_retries_transient_gateway_errors() -> None:
    from scripts.build_dashboard_seed import _langfuse_get

    calls = {"count": 0}

    class _FakeClient:
        def get(
            self,
            path: str,
            params: object = None,
            timeout: float = 30.0,
        ) -> httpx.Response:
            del params, timeout
            calls["count"] += 1
            if calls["count"] < 3:
                return httpx.Response(502, request=httpx.Request("GET", f"https://example{path}"))
            return httpx.Response(
                200,
                json={"ok": True},
                request=httpx.Request("GET", f"https://example{path}"),
            )

    with patch("scripts.build_dashboard_seed.time.sleep"):
        response = _langfuse_get(
            _FakeClient(),  # type: ignore[arg-type]
            "/api/public/observations",
        )

    assert response.status_code == 200
    assert calls["count"] == 3


@pytest.mark.unit
def test_collect_trace_ids_applies_client_side_min_occurred_at_cutoff() -> None:
    from datetime import datetime, timezone

    from scripts.build_dashboard_seed import _collect_trace_ids_by_observation_name

    captured: dict[str, object] = {}

    def fake_iter_paginated(
        client: object,
        path: str,
        *,
        base_params: dict[str, object] | None = None,
        page_size: int = 100,
        stop_on_start_time_before: datetime | None = None,
    ) -> list[dict[str, object]]:
        del client, path, page_size
        captured["base_params"] = base_params
        captured["stop_on_start_time_before"] = stop_on_start_time_before
        return [{"traceId": "trace-1", "startTime": "2026-05-26T10:00:00Z"}]

    cutoff = datetime(2026, 5, 25, tzinfo=timezone.utc)
    with patch(
        "scripts.build_dashboard_seed._iter_paginated",
        side_effect=fake_iter_paginated,
    ):
        trace_ids = _collect_trace_ids_by_observation_name(
            cast(httpx.Client, object()),
            observation_name="triage_issue_pipeline",
            page_size=50,
            min_occurred_at=cutoff,
        )

    assert trace_ids == ["trace-1"]
    base_params = cast(dict[str, str], captured["base_params"])
    assert base_params == {"name": "triage_issue_pipeline"}
    assert captured["stop_on_start_time_before"] == cutoff


@pytest.mark.unit
def test_export_backfill_skips_traces_before_min_occurred_at() -> None:
    from datetime import datetime, timezone

    from scripts import build_dashboard_seed as subject

    def fake_iter_paginated(
        client: object,
        path: str,
        *,
        base_params: dict[str, object] | None = None,
        page_size: int = 100,
        stop_on_start_time_before: datetime | None = None,
    ) -> list[dict[str, object]]:
        del client, page_size, stop_on_start_time_before
        if (
            path == "/api/public/observations"
            and isinstance(base_params, dict)
            and base_params.get("name") == "triage_issue_pipeline"
        ):
            return [{"traceId": "trace-new", "startTime": "2026-05-26T10:00:00Z"}]
        if path == "/api/public/observations" and base_params == {"traceId": "trace-new"}:
            return [
                _story_cls_observation(
                    input_text="Current issue type: Bug\nCurrent priority: P2",
                    reason="ok",
                    confidence=0.9,
                ),
            ]
        if path == "/api/public/observations" and base_params == {"traceId": "trace-old"}:
            raise AssertionError("trace-old should not be fetched after min_occurred_at filter")
        raise AssertionError(f"Unexpected pagination call: path={path}, base_params={base_params}")

    creds = subject.LangfuseCredentials(
        public_key="pk",
        secret_key="sk",
        base_url="https://langfuse.example",
    )
    fake_client = _BackfillFakeClient(
        {
            "trace-new": {
                "id": "trace-new",
                "timestamp": "2026-05-26T10:00:00Z",
                "metadata": {"run_id": "run-new", "issue_key": "BC-1", "project": "BC"},
            },
            "trace-old": {
                "id": "trace-old",
                "timestamp": "2026-05-20T10:00:00Z",
                "metadata": {"run_id": "run-old", "issue_key": "BC-2", "project": "BC"},
            },
        },
    )
    with (
        patch("scripts.build_dashboard_seed._read_langfuse_credentials", return_value=creds),
        patch(
            "scripts.build_dashboard_seed._iter_paginated",
            side_effect=fake_iter_paginated,
        ),
        patch("scripts.build_dashboard_seed.httpx.Client", return_value=fake_client),
        patch("scripts.build_dashboard_seed.tqdm", side_effect=lambda it, **_: it),
    ):
        rows = subject.export_backfill(
            page_size=50,
            latest_only=False,
            default_source="bug_created",
            trace_name="triage_issue_pipeline",
            max_records=None,
            enrich_jira_created=False,
            min_occurred_at=datetime(2026, 5, 25, tzinfo=timezone.utc),
        )

    assert len(rows) == 1
    assert rows[0]["run_id"] == "run-new"
    assert fake_client.trace_get_calls == ["/api/public/traces/trace-new"]


@pytest.mark.unit
def test_row_matches_project_filter_accepts_bc_rows() -> None:
    from scripts.build_dashboard_seed import row_matches_project_filter

    row = {"project": "BC", "issue_key": "BC-13246"}
    assert row_matches_project_filter(row, "BC") is True


@pytest.mark.unit
def test_row_matches_project_filter_rejects_non_bc_project_or_key() -> None:
    from scripts.build_dashboard_seed import row_matches_project_filter

    assert row_matches_project_filter({"project": "TJC", "issue_key": "TJC-1"}, "BC") is False
    assert row_matches_project_filter({"project": "BC", "issue_key": "TJC-1"}, "BC") is False
    assert row_matches_project_filter({"project": "BC", "issue_key": "BC-abc"}, "BC") is False


@pytest.mark.unit
def test_row_matches_project_filter_accepts_comma_separated_projects() -> None:
    from scripts.build_dashboard_seed import row_matches_project_filter

    assert row_matches_project_filter({"project": "BC", "issue_key": "BC-1"}, "BC,TJC") is True
    assert row_matches_project_filter({"project": "TJC", "issue_key": "TJC-99"}, "BC, TJC") is True
    assert row_matches_project_filter({"project": "TJC", "issue_key": "TJC-1"}, "BC") is False


@pytest.mark.unit
def test_row_matches_project_filter_empty_means_all_projects() -> None:
    from scripts.build_dashboard_seed import row_matches_project_filter

    assert row_matches_project_filter({"project": "TJC", "issue_key": "TJC-1"}, "") is True


@pytest.mark.unit
def test_enrich_rows_with_issue_created_at_attaches_field() -> None:
    from scripts.build_dashboard_seed import enrich_rows_with_issue_created_at

    rows = [
        {"issue_key": "BC-1", "triaged_at": "2026-06-02T10:00:00Z"},
        {"issue_key": "BC-2", "triaged_at": "2026-06-02T11:00:00Z"},
    ]

    from triage_service.adapters.jira_issue_fetcher import IssueDecisionMetadata

    class _FakeFetcher:
        def fetch_decision_metadata(self, issue_key: str, *, run_id: str) -> IssueDecisionMetadata:
            _ = run_id
            by_key = {
                "BC-1": IssueDecisionMetadata(
                    issue_created_at="2026-05-01T08:15:30.123Z",
                    issue_name="Issue one",
                ),
                "BC-2": IssueDecisionMetadata(
                    issue_created_at="2026-05-02T09:00:00.000Z",
                    issue_name="Issue two",
                ),
            }
            return by_key[issue_key]

    with (
        patch("scripts.build_dashboard_seed.tqdm", side_effect=lambda it, **_: it),
        patch("triage_service.core.settings.load_settings", return_value=object()),
        patch(
            "triage_service.adapters.jira_issue_fetcher.JiraIssueFetcher",
            return_value=_FakeFetcher(),
        ),
        patch("scripts.build_dashboard_seed._ensure_triage_import_path"),
    ):
        enriched = enrich_rows_with_issue_created_at(rows, env_file=None)

    assert enriched[0]["issue_created_at"] == "2026-05-01T08:15:30.123Z"
    assert enriched[0]["issue_name"] == "Issue one"
    assert enriched[1]["issue_created_at"] == "2026-05-02T09:00:00.000Z"
    assert enriched[1]["issue_name"] == "Issue two"
    assert enriched[0]["triaged_at"] == "2026-06-02T10:00:00Z"
    assert "occurred_at" not in enriched[0]


@pytest.mark.unit
def test_latest_per_issue_keeps_latest_timestamp() -> None:
    from scripts.build_dashboard_seed import latest_per_issue

    rows = [
        {"issue_key": "TJC-1", "triaged_at": "2026-05-01T10:00:00Z", "run_id": "a"},
        {"issue_key": "TJC-1", "triaged_at": "2026-05-02T10:00:00Z", "run_id": "b"},
        {"issue_key": "TJC-2", "triaged_at": "2026-05-01T09:00:00Z", "run_id": "c"},
    ]

    deduped = latest_per_issue(rows)
    by_issue = {str(row["issue_key"]): row for row in deduped}
    assert by_issue["TJC-1"]["run_id"] == "b"
    assert by_issue["TJC-2"]["run_id"] == "c"


@pytest.mark.unit
def test_exclude_blacklisted_issues_drops_matching_keys() -> None:
    from scripts.build_dashboard_seed import exclude_blacklisted_issues

    rows = [
        {"issue_key": "BC-22932", "run_id": "a"},
        {"issue_key": "BC-100", "run_id": "b"},
        {"issue_key": "bc-22932", "run_id": "c"},
    ]
    filtered = exclude_blacklisted_issues(rows, excluded_issues=frozenset({"BC-22932"}))
    assert [row["run_id"] for row in filtered] == ["b"]


@pytest.mark.unit
def test_backfill_main_passes_default_excluded_issues(tmp_path: Path) -> None:
    from scripts.build_dashboard_seed import main

    output = tmp_path / "decisions.json"
    with patch("scripts.build_dashboard_seed.export_backfill", return_value=[]) as mock_export:
        rc = main(
            [
                "--env-file",
                str(tmp_path / "missing.env"),
                "backfill",
                "--output",
                str(output),
                "--no-jira-created",
                "--no-min-occurred-at",
            ],
        )

    assert rc == 0
    assert mock_export.call_args.kwargs["excluded_issues"] == frozenset({"BC-22932"})


@pytest.mark.unit
def test_main_writes_output_json(tmp_path: Path) -> None:
    from scripts.build_dashboard_seed import main

    output = tmp_path / "decisions.json"
    rows = [
        {
            "event_type": "triage_completed",
            "run_id": "run-3",
            "session_id": "run-3",
            "issue_key": "TJC-3",
            "project": "TJC",
            "source": "bug_created",
            "intake_issue_type": "Bug",
            "intake_priority": "P2",
            "recommended_issue_type": "Story",
            "recommended_priority": None,
            "applied_type_change": False,
            "applied_priority_change": False,
            "confidence": 0.8,
            "reason": "Story path",
            "triaged_at": "2026-06-02T12:00:00Z",
        },
    ]

    env = str(tmp_path / "missing.env")
    with patch("scripts.build_dashboard_seed.export_backfill", return_value=rows):
        rc = main(["--env-file", env, "backfill", "--output", str(output)])
    assert rc == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload[0]["session_id"] == "run-3"


class _BackfillFakeResponse:
    status_code = 200

    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self._payload


class _BackfillFakeClient:
    def __init__(self, trace_payloads: dict[str, dict[str, object]]) -> None:
        self._trace_payloads = trace_payloads
        self.trace_get_calls: list[str] = []

    def __enter__(self) -> "_BackfillFakeClient":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def get(self, path: str, params: object = None, timeout: float = 30.0) -> _BackfillFakeResponse:
        del params, timeout
        for trace_id, payload in self._trace_payloads.items():
            expected = f"/api/public/traces/{trace_id}"
            if path == expected:
                self.trace_get_calls.append(path)
                return _BackfillFakeResponse(payload)
        raise AssertionError(f"Unexpected GET path: {path}")


def _story_cls_observation(*, input_text: str, reason: str, confidence: float) -> dict[str, object]:
    return {
        "name": "inference_classification",
        "input": input_text,
        "metadata": {
            "parsed": {
                "recommended_issue_type": "Story",
                "confidence": confidence,
                "reason": reason,
            },
        },
    }


@pytest.mark.unit
def test_export_backfill_collects_trace_ids_from_scoped_observations() -> None:
    from scripts import build_dashboard_seed as subject

    def fake_iter_paginated(
        client: object,
        path: str,
        *,
        base_params: dict[str, object] | None = None,
        page_size: int = 100,
        stop_on_start_time_before: object = None,
    ) -> list[dict[str, object]]:
        del client, page_size, stop_on_start_time_before
        if (
            path == "/api/public/observations"
            and isinstance(base_params, dict)
            and base_params.get("name") == "triage_issue_pipeline"
        ):
            return [{"traceId": "trace-123", "startTime": "2026-06-02T15:53:16.037Z"}]
        if path == "/api/public/observations" and base_params == {"traceId": "trace-123"}:
            return [
                _story_cls_observation(
                    input_text="Current issue type: Bug\nCurrent priority: P2",
                    reason="Not a defect.",
                    confidence=0.88,
                ),
            ]
        raise AssertionError(f"Unexpected pagination call: path={path}, base_params={base_params}")

    creds = subject.LangfuseCredentials(
        public_key="pk",
        secret_key="sk",
        base_url="https://langfuse.example",
    )
    fake_client = _BackfillFakeClient(
        {
            "trace-123": {
                "id": "trace-123",
                "timestamp": "2026-06-02T15:53:16.037Z",
                "metadata": {
                    "run_id": "run-123",
                    "issue_key": "BC-123",
                    "project": "BC",
                },
            },
        },
    )
    patch_iter = patch(
        "scripts.build_dashboard_seed._iter_paginated",
        side_effect=fake_iter_paginated,
    )
    with (
        patch("scripts.build_dashboard_seed._read_langfuse_credentials", return_value=creds),
        patch_iter,
        patch("scripts.build_dashboard_seed.httpx.Client", return_value=fake_client),
    ):
        rows = subject.export_backfill(
            page_size=50,
            latest_only=False,
            default_source="bug_created",
            trace_name="triage_issue_pipeline",
            max_records=None,
            enrich_jira_created=False,
        )

    assert len(rows) == 1
    assert rows[0]["run_id"] == "run-123"
    assert rows[0]["issue_key"] == "BC-123"


@pytest.mark.unit
def test_export_backfill_applies_max_records_limit() -> None:
    from scripts import build_dashboard_seed as subject

    def fake_iter_paginated(
        client: object,
        path: str,
        *,
        base_params: dict[str, object] | None = None,
        page_size: int = 100,
        stop_on_start_time_before: object = None,
    ) -> list[dict[str, object]]:
        del client, page_size, stop_on_start_time_before
        if (
            path == "/api/public/observations"
            and isinstance(base_params, dict)
            and base_params.get("name") == "triage_issue_pipeline"
        ):
            return [{"traceId": "trace-1"}, {"traceId": "trace-2"}]
        if path == "/api/public/observations" and base_params == {"traceId": "trace-1"}:
            return [
                _story_cls_observation(
                    input_text="Current Jira issue type: Bug\nCurrent Jira priority: P2",
                    reason="r1",
                    confidence=0.9,
                ),
            ]
        if path == "/api/public/observations" and base_params == {"traceId": "trace-2"}:
            return [
                _story_cls_observation(
                    input_text="Current Jira issue type: Bug\nCurrent Jira priority: P3",
                    reason="r2",
                    confidence=0.8,
                ),
            ]
        raise AssertionError(f"Unexpected pagination call: path={path}, base_params={base_params}")

    creds = subject.LangfuseCredentials(
        public_key="pk",
        secret_key="sk",
        base_url="https://langfuse.example",
    )
    fake_client = _BackfillFakeClient(
        {
            "trace-1": {
                "id": "trace-1",
                "timestamp": "2026-06-02T15:53:16.037Z",
                "metadata": {"run_id": "run-1", "issue_key": "BC-1", "project": "BC"},
            },
            "trace-2": {
                "id": "trace-2",
                "timestamp": "2026-06-02T15:54:16.037Z",
                "metadata": {"run_id": "run-2", "issue_key": "BC-2", "project": "BC"},
            },
        },
    )
    with (
        patch("scripts.build_dashboard_seed._read_langfuse_credentials", return_value=creds),
        patch(
            "scripts.build_dashboard_seed._iter_paginated",
            side_effect=fake_iter_paginated,
        ),
        patch("scripts.build_dashboard_seed.httpx.Client", return_value=fake_client),
        patch("scripts.build_dashboard_seed.tqdm", side_effect=lambda it, **_: it),
    ):
        rows = subject.export_backfill(
            page_size=50,
            latest_only=False,
            default_source="bug_created",
            trace_name="triage_issue_pipeline",
            max_records=1,
            enrich_jira_created=False,
        )

    assert len(rows) == 1
    assert rows[0]["run_id"] == "run-1"
    assert rows[0]["issue_key"] == "BC-1"
    assert fake_client.trace_get_calls == ["/api/public/traces/trace-1"]


@pytest.mark.unit
def test_export_backfill_filters_out_non_bc_traces() -> None:
    from scripts import build_dashboard_seed as subject

    def fake_iter_paginated(
        client: object,
        path: str,
        *,
        base_params: dict[str, object] | None = None,
        page_size: int = 100,
        stop_on_start_time_before: object = None,
    ) -> list[dict[str, object]]:
        del client, page_size, stop_on_start_time_before
        if (
            path == "/api/public/observations"
            and isinstance(base_params, dict)
            and base_params.get("name") == "triage_issue_pipeline"
        ):
            return [{"traceId": "trace-tjc"}]
        if path == "/api/public/observations" and base_params == {"traceId": "trace-tjc"}:
            return [
                _story_cls_observation(
                    input_text="Current issue type: Bug\nCurrent priority: P2",
                    reason="r",
                    confidence=0.9,
                ),
            ]
        raise AssertionError(f"Unexpected pagination call: path={path}, base_params={base_params}")

    creds = subject.LangfuseCredentials(
        public_key="pk",
        secret_key="sk",
        base_url="https://langfuse.example",
    )
    fake_client = _BackfillFakeClient(
        {
            "trace-tjc": {
                "id": "trace-tjc",
                "timestamp": "2026-06-02T15:53:16.037Z",
                "metadata": {"run_id": "run-tjc", "issue_key": "TJC-9", "project": "TJC"},
            },
        },
    )
    with (
        patch("scripts.build_dashboard_seed._read_langfuse_credentials", return_value=creds),
        patch(
            "scripts.build_dashboard_seed._iter_paginated",
            side_effect=fake_iter_paginated,
        ),
        patch("scripts.build_dashboard_seed.httpx.Client", return_value=fake_client),
        patch("scripts.build_dashboard_seed.tqdm", side_effect=lambda it, **_: it),
    ):
        rows = subject.export_backfill(
            page_size=50,
            latest_only=False,
            default_source="bug_created",
            trace_name="triage_issue_pipeline",
            max_records=None,
            project_filter="BC",
            enrich_jira_created=False,
        )

    assert rows == []
