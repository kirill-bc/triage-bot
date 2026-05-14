"""Unit tests for consistent oversized payload truncation in observability paths."""

from __future__ import annotations

import pytest


@pytest.mark.unit
def test_truncate_payload_tree_leaves_small_strings_unchanged() -> None:
    from triage_service.observability.log_payload_guard import (
        DEFAULT_MAX_LOG_STRING_CHARS,
        truncate_payload_tree,
    )

    data = {"a": "hi", "n": 3}
    out, truncated = truncate_payload_tree(data, max_string_chars=DEFAULT_MAX_LOG_STRING_CHARS)
    assert truncated is False
    assert out == data


@pytest.mark.unit
def test_truncate_payload_tree_truncates_long_string_and_sets_root_flag() -> None:
    from triage_service.observability.log_payload_guard import truncate_payload_tree

    long_reason = "x" * 100
    data = {
        "event_type": "classification_completed",
        "run_id": "r1",
        "reason": long_reason,
    }
    out, truncated = truncate_payload_tree(data, max_string_chars=40)
    assert truncated is True
    assert out["log_payload_truncated"] is True
    assert len(out["reason"]) < len(long_reason)
    assert "truncated" in out["reason"]
    assert "100 chars total" in out["reason"]


@pytest.mark.unit
def test_truncate_payload_tree_nested_strings_and_lists() -> None:
    from triage_service.observability.log_payload_guard import truncate_payload_tree

    blob = "y" * 50
    data = {
        "telemetry": {"detail": blob, "ok": True},
        "items": [{"msg": blob}, "short"],
    }
    out, truncated = truncate_payload_tree(data, max_string_chars=20)
    assert truncated is True
    assert out["log_payload_truncated"] is True
    assert "truncated" in out["telemetry"]["detail"]
    assert "truncated" in out["items"][0]["msg"]
    assert out["items"][1] == "short"


@pytest.mark.unit
def test_truncate_payload_tree_does_not_mutate_original() -> None:
    from triage_service.observability.log_payload_guard import truncate_payload_tree

    inner = {"msg": "z" * 30}
    data = {"nested": inner}
    out, _ = truncate_payload_tree(data, max_string_chars=10)
    assert len(inner["msg"]) == 30
    assert "truncated" in out["nested"]["msg"]


@pytest.mark.unit
def test_preview_bytes_for_log_matches_truncation_marker_style() -> None:
    from triage_service.observability.log_payload_guard import preview_bytes_for_log

    body = b"a" * 200
    text = preview_bytes_for_log(body, max_bytes=50)
    assert "truncated" in text
    assert "200 bytes total" in text
