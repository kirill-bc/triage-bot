"""Local manual CLI triage: project inference and ``source=manual_cli`` orchestration."""

from __future__ import annotations

import pytest

from triage_service.core.triage_fallback import TriageFailure, fallback_for_exception
from triage_service.core.triage_recommendation_parser import TriageRecommendation


@pytest.mark.unit
@pytest.mark.parametrize(
    ("issue_key", "expected"),
    [
        ("TJC-1", "TJC"),
        ("TJC-42", "TJC"),
        ("BC-999", "BC"),
    ],
)
def test_infer_project_from_issue_key_parses_standard_keys(issue_key: str, expected: str) -> None:
    from triage_manual_cli import infer_project_from_issue_key

    assert infer_project_from_issue_key(issue_key) == expected


@pytest.mark.unit
def test_infer_project_from_issue_key_rejects_missing_hyphen() -> None:
    from triage_manual_cli import infer_project_from_issue_key

    with pytest.raises(ValueError, match="-"):
        infer_project_from_issue_key("TJC")


@pytest.mark.unit
def test_infer_project_from_issue_key_rejects_non_numeric_suffix() -> None:
    from triage_manual_cli import infer_project_from_issue_key

    with pytest.raises(ValueError, match="numeric"):
        infer_project_from_issue_key("TJC-abc")


@pytest.mark.unit
def test_run_cli_triage_passes_manual_cli_source_to_runner() -> None:
    import uuid

    from triage_manual_cli import run_cli_triage

    calls: list[tuple[str, str, str, str]] = []

    class _RecordingRunner:
        def run_sync(
            self,
            issue_key: str,
            project: str,
            source: str,
            *,
            run_id: str,
        ) -> TriageRecommendation | TriageFailure:
            calls.append((issue_key, project, source, run_id))
            return TriageRecommendation(
                recommended_issue_type="Story",
                recommended_priority=None,
                confidence=0.9,
                reason="stub",
            )

    outcome = run_cli_triage("TJC-7", runner=_RecordingRunner())
    assert isinstance(outcome, TriageRecommendation)
    assert calls[0][:3] == ("TJC-7", "TJC", "manual_trigger")
    uuid.UUID(calls[0][3])


@pytest.mark.unit
def test_run_cli_triage_calls_flush_inference_telemetry_when_runner_exposes_it() -> None:
    from triage_manual_cli import run_cli_triage

    flush_calls = 0

    class _RunnerWithFlush:
        def run_sync(
            self,
            issue_key: str,
            project: str,
            source: str,
            *,
            run_id: str,
        ) -> TriageRecommendation | TriageFailure:
            _ = (issue_key, project, source, run_id)
            return TriageRecommendation(
                recommended_issue_type="Story",
                recommended_priority=None,
                confidence=0.5,
                reason="flush stub",
            )

        def flush_inference_telemetry(self) -> None:
            nonlocal flush_calls
            flush_calls += 1

    _ = run_cli_triage("TJC-7", runner=_RunnerWithFlush())
    assert flush_calls == 1


@pytest.mark.unit
def test_run_cli_triage_uses_explicit_project_when_given() -> None:
    import uuid

    from triage_manual_cli import run_cli_triage

    calls: list[tuple[str, str, str, str]] = []

    class _RecordingRunner:
        def run_sync(
            self,
            issue_key: str,
            project: str,
            source: str,
            *,
            run_id: str,
        ) -> TriageRecommendation | TriageFailure:
            calls.append((issue_key, project, source, run_id))
            return fallback_for_exception(RuntimeError("x"))

    outcome = run_cli_triage("TJC-7", project="BC", runner=_RecordingRunner())
    assert isinstance(outcome, TriageFailure)
    assert calls[0][:3] == ("TJC-7", "BC", "manual_trigger")
    uuid.UUID(calls[0][3])
