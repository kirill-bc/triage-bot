"""Unit tests for strict triage model output parsing and validation."""

from __future__ import annotations

import json

import pytest

from triage_recommendation_parser import (
    InvalidTriageRecommendationError,
    parse_triage_recommendation_text,
)


def _bug_payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "recommended_issue_type": "Bug",
        "recommended_priority": "P2",
        "confidence": 0.75,
        "reason": "Matches bug criteria and severity.",
    }
    base.update(overrides)
    return base


def _story_payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "recommended_issue_type": "Story",
        "recommended_priority": None,
        "confidence": 0.6,
        "reason": "Feature request phrasing; not a defect.",
    }
    base.update(overrides)
    return base


@pytest.mark.unit
def test_parse_bug_path_accepts_valid_merged_response() -> None:
    raw = json.dumps(_bug_payload())
    rec = parse_triage_recommendation_text(raw)
    assert rec.recommended_issue_type == "Bug"
    assert rec.recommended_priority == "P2"
    assert rec.confidence == 0.75
    assert "bug" in rec.reason.lower() or "severity" in rec.reason.lower()


@pytest.mark.unit
def test_parse_story_path_accepts_null_priority() -> None:
    raw = json.dumps(_story_payload())
    rec = parse_triage_recommendation_text(raw)
    assert rec.recommended_issue_type == "Story"
    assert rec.recommended_priority is None


@pytest.mark.unit
def test_parse_story_path_accepts_omitted_priority_key() -> None:
    data = _story_payload()
    del data["recommended_priority"]
    rec = parse_triage_recommendation_text(json.dumps(data))
    assert rec.recommended_issue_type == "Story"
    assert rec.recommended_priority is None


@pytest.mark.unit
def test_parse_rejects_bug_without_priority() -> None:
    data = _bug_payload()
    del data["recommended_priority"]
    with pytest.raises(InvalidTriageRecommendationError):
        parse_triage_recommendation_text(json.dumps(data))


@pytest.mark.unit
def test_parse_rejects_bug_with_null_priority() -> None:
    data = _bug_payload(recommended_priority=None)
    with pytest.raises(InvalidTriageRecommendationError):
        parse_triage_recommendation_text(json.dumps(data))


@pytest.mark.unit
def test_parse_rejects_story_with_priority_set() -> None:
    data = _story_payload(recommended_priority="P1")
    with pytest.raises(InvalidTriageRecommendationError):
        parse_triage_recommendation_text(json.dumps(data))


@pytest.mark.unit
def test_parse_rejects_invalid_issue_type() -> None:
    data = _bug_payload(recommended_issue_type="Task")
    with pytest.raises(InvalidTriageRecommendationError):
        parse_triage_recommendation_text(json.dumps(data))


@pytest.mark.unit
def test_parse_rejects_invalid_priority_label_for_bug() -> None:
    data = _bug_payload(recommended_priority="P5")
    with pytest.raises(InvalidTriageRecommendationError):
        parse_triage_recommendation_text(json.dumps(data))


@pytest.mark.unit
def test_parse_rejects_confidence_below_zero() -> None:
    data = _bug_payload(confidence=-0.01)
    with pytest.raises(InvalidTriageRecommendationError):
        parse_triage_recommendation_text(json.dumps(data))


@pytest.mark.unit
def test_parse_rejects_confidence_above_one() -> None:
    data = _bug_payload(confidence=1.01)
    with pytest.raises(InvalidTriageRecommendationError):
        parse_triage_recommendation_text(json.dumps(data))


@pytest.mark.unit
def test_parse_accepts_confidence_at_bounds() -> None:
    low = parse_triage_recommendation_text(json.dumps(_bug_payload(confidence=0.0)))
    assert low.confidence == 0.0
    high = parse_triage_recommendation_text(json.dumps(_bug_payload(confidence=1.0)))
    assert high.confidence == 1.0


@pytest.mark.unit
def test_parse_rejects_empty_reason() -> None:
    data = _bug_payload(reason="")
    with pytest.raises(InvalidTriageRecommendationError):
        parse_triage_recommendation_text(json.dumps(data))


@pytest.mark.unit
def test_parse_rejects_whitespace_only_reason() -> None:
    data = _bug_payload(reason="   \t\n")
    with pytest.raises(InvalidTriageRecommendationError):
        parse_triage_recommendation_text(json.dumps(data))


@pytest.mark.unit
def test_parse_ignores_legacy_recommended_action_field() -> None:
    data = _bug_payload()
    data["recommended_action"] = "delete_issue"
    rec = parse_triage_recommendation_text(json.dumps(data))
    assert rec.recommended_issue_type == "Bug"


@pytest.mark.unit
def test_parse_from_text_raises_on_invalid_json() -> None:
    with pytest.raises(InvalidTriageRecommendationError):
        parse_triage_recommendation_text("not json")


@pytest.mark.unit
def test_parse_from_text_raises_on_non_object_json() -> None:
    with pytest.raises(InvalidTriageRecommendationError):
        parse_triage_recommendation_text("[1, 2]")


@pytest.mark.unit
def test_parse_strips_surrounding_whitespace_on_payload() -> None:
    inner = json.dumps(_bug_payload())
    raw = f"\n  {inner}  \n"
    rec = parse_triage_recommendation_text(raw)
    assert rec.recommended_issue_type == "Bug"
