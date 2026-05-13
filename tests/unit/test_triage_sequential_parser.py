"""Unit tests for classification-only and priority-only model JSON parsing."""

from __future__ import annotations

import json

import pytest

from triage_service.core.triage_recommendation_parser import (
    ClassificationStepOutput,
    InvalidTriageRecommendationError,
    classification_bug_to_final,
    classification_story_to_final,
    merge_bug_classification_with_priority,
    parse_classification_step_text,
    parse_priority_step_text,
)


@pytest.mark.unit
def test_parse_classification_drops_legacy_recommended_action() -> None:
    raw = json.dumps(
        {
            "recommended_issue_type": "Bug",
            "confidence": 0.5,
            "reason": "ok",
            "recommended_action": "reclassify",
        },
    )
    step = parse_classification_step_text(raw)
    assert step.recommended_issue_type == "Bug"


@pytest.mark.unit
def test_parse_classification_story_round_trip() -> None:
    raw = json.dumps(
        {
            "recommended_issue_type": "Story",
            "confidence": 0.82,
            "reason": "Support narrative, no defect.",
        },
    )
    step = parse_classification_step_text(raw)
    assert step.recommended_issue_type == "Story"
    assert step.recommended_priority is None
    final = classification_story_to_final(step)
    assert final.recommended_issue_type == "Story"
    assert final.recommended_priority is None
    assert final.confidence == 0.82


@pytest.mark.unit
def test_parse_classification_bug_without_priority() -> None:
    raw = json.dumps(
        {
            "recommended_issue_type": "Bug",
            "confidence": 0.5,
            "reason": "Reproducible defect.",
        },
    )
    step = parse_classification_step_text(raw)
    assert step.recommended_issue_type == "Bug"
    assert step.recommended_priority is None


@pytest.mark.unit
def test_parse_classification_bug_rejects_non_null_priority() -> None:
    raw = json.dumps(
        {
            "recommended_issue_type": "Bug",
            "recommended_priority": "P2",
            "confidence": 0.5,
            "reason": "x",
        },
    )
    with pytest.raises(InvalidTriageRecommendationError):
        parse_classification_step_text(raw)


@pytest.mark.unit
def test_parse_classification_story_rejects_priority_field() -> None:
    raw = json.dumps(
        {
            "recommended_issue_type": "Story",
            "recommended_priority": "P1",
            "confidence": 0.1,
            "reason": "x",
        },
    )
    with pytest.raises(InvalidTriageRecommendationError):
        parse_classification_step_text(raw)


@pytest.mark.unit
def test_parse_priority_step_and_merge_bug_path() -> None:
    cls = ClassificationStepOutput(
        recommended_issue_type="Bug",
        recommended_priority=None,
        confidence=0.4,
        reason="classification reason",
    )
    raw = json.dumps(
        {
            "recommended_priority": "P3",
            "confidence": 0.91,
            "reason": "Severity per policy.",
        },
    )
    pri = parse_priority_step_text(raw)
    merged = merge_bug_classification_with_priority(cls, pri)
    assert merged.recommended_issue_type == "Bug"
    assert merged.recommended_priority == "P3"
    assert merged.confidence == 0.91
    assert merged.reason == "Severity per policy."


@pytest.mark.unit
def test_classification_bug_to_final_requires_priority() -> None:
    cls = ClassificationStepOutput(
        recommended_issue_type="Bug",
        recommended_priority=None,
        confidence=0.4,
        reason="x",
    )
    with pytest.raises(ValueError, match="priority"):
        classification_bug_to_final(cls)


@pytest.mark.unit
def test_parse_priority_step_rejects_invalid_priority_name() -> None:
    raw = json.dumps(
        {
            "recommended_priority": "High",
            "confidence": 0.5,
            "reason": "x",
        },
    )
    with pytest.raises(InvalidTriageRecommendationError):
        parse_priority_step_text(raw)
