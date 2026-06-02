"""Unit tests for scripts/backfill_langfuse_decisions.py."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.mark.unit
def test_extract_intake_from_prompt_parses_issue_type_and_priority() -> None:
    from scripts.backfill_langfuse_decisions import _extract_intake_from_prompt

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
    from scripts.backfill_langfuse_decisions import build_decision_row

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


@pytest.mark.unit
def test_build_decision_row_bug_uses_priority_generation_and_sums_session_cost() -> None:
    from scripts.backfill_langfuse_decisions import build_decision_row

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
def test_latest_per_issue_keeps_latest_timestamp() -> None:
    from scripts.backfill_langfuse_decisions import latest_per_issue

    rows = [
        {"issue_key": "TJC-1", "occurred_at": "2026-05-01T10:00:00Z", "run_id": "a"},
        {"issue_key": "TJC-1", "occurred_at": "2026-05-02T10:00:00Z", "run_id": "b"},
        {"issue_key": "TJC-2", "occurred_at": "2026-05-01T09:00:00Z", "run_id": "c"},
    ]

    deduped = latest_per_issue(rows)
    by_issue = {str(row["issue_key"]): row for row in deduped}
    assert by_issue["TJC-1"]["run_id"] == "b"
    assert by_issue["TJC-2"]["run_id"] == "c"


@pytest.mark.unit
def test_main_writes_output_json(tmp_path: Path) -> None:
    from scripts.backfill_langfuse_decisions import main

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
            "occurred_at": "2026-06-02T12:00:00Z",
        },
    ]

    with patch("scripts.backfill_langfuse_decisions.export_backfill", return_value=rows):
        rc = main(["--output", str(output), "--env-file", str(tmp_path / "missing.env")])
    assert rc == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload[0]["session_id"] == "run-3"


class _BackfillFakeResponse:
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
    from scripts import backfill_langfuse_decisions as subject

    def fake_iter_paginated(
        client: object,
        path: str,
        *,
        base_params: dict[str, object] | None = None,
        page_size: int = 100,
    ) -> list[dict[str, object]]:
        del client, page_size
        if path == "/api/public/observations" and base_params == {"name": "triage_issue_pipeline"}:
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
                    "issue_key": "TJC-123",
                    "project": "TJC",
                },
            },
        },
    )
    patch_iter = patch(
        "scripts.backfill_langfuse_decisions._iter_paginated",
        side_effect=fake_iter_paginated,
    )
    with (
        patch("scripts.backfill_langfuse_decisions._read_langfuse_credentials", return_value=creds),
        patch_iter,
        patch("scripts.backfill_langfuse_decisions.httpx.Client", return_value=fake_client),
    ):
        rows = subject.export_backfill(
            page_size=50,
            latest_only=False,
            default_source="bug_created",
            trace_name="triage_issue_pipeline",
            max_records=None,
        )

    assert len(rows) == 1
    assert rows[0]["run_id"] == "run-123"


@pytest.mark.unit
def test_export_backfill_applies_max_records_limit() -> None:
    from scripts import backfill_langfuse_decisions as subject

    def fake_iter_paginated(
        client: object,
        path: str,
        *,
        base_params: dict[str, object] | None = None,
        page_size: int = 100,
    ) -> list[dict[str, object]]:
        del client, page_size
        if path == "/api/public/observations" and base_params == {"name": "triage_issue_pipeline"}:
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
                "metadata": {"run_id": "run-1", "issue_key": "TJC-1", "project": "TJC"},
            },
            "trace-2": {
                "id": "trace-2",
                "timestamp": "2026-06-02T15:54:16.037Z",
                "metadata": {"run_id": "run-2", "issue_key": "TJC-2", "project": "TJC"},
            },
        },
    )
    with (
        patch("scripts.backfill_langfuse_decisions._read_langfuse_credentials", return_value=creds),
        patch(
            "scripts.backfill_langfuse_decisions._iter_paginated",
            side_effect=fake_iter_paginated,
        ),
        patch("scripts.backfill_langfuse_decisions.httpx.Client", return_value=fake_client),
        patch("scripts.backfill_langfuse_decisions.tqdm", side_effect=lambda it, **_: it),
    ):
        rows = subject.export_backfill(
            page_size=50,
            latest_only=False,
            default_source="bug_created",
            trace_name="triage_issue_pipeline",
            max_records=1,
        )

    assert len(rows) == 1
    assert rows[0]["run_id"] == "run-1"
    assert fake_client.trace_get_calls == ["/api/public/traces/trace-1"]
