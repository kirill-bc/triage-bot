"""Gate: GitHub Actions CI workflow file exists and defines required quality jobs."""

from pathlib import Path

import pytest


@pytest.mark.lint
def test_ci_workflow_file_exists() -> None:
    root = Path(__file__).resolve().parents[2]
    workflow = root / ".github" / "workflows" / "ci.yml"
    assert workflow.exists(), (
        f"CI workflow not found at {workflow}. "
        "Create .github/workflows/ci.yml with mypy, lint, and unit+integration gates."
    )


@pytest.mark.lint
def test_ci_workflow_contains_mypy_gate() -> None:
    root = Path(__file__).resolve().parents[2]
    workflow = root / ".github" / "workflows" / "ci.yml"
    content = workflow.read_text(encoding="utf-8")
    assert "mypy" in content, "CI workflow must include a mypy type-checking step."


@pytest.mark.lint
def test_ci_workflow_contains_lint_gate() -> None:
    root = Path(__file__).resolve().parents[2]
    workflow = root / ".github" / "workflows" / "ci.yml"
    content = workflow.read_text(encoding="utf-8")
    assert "pytest -m lint" in content or 'pytest -m "lint"' in content, (
        "CI workflow must include a pytest -m lint step."
    )


@pytest.mark.lint
def test_ci_workflow_contains_unit_and_integration_gate() -> None:
    root = Path(__file__).resolve().parents[2]
    workflow = root / ".github" / "workflows" / "ci.yml"
    content = workflow.read_text(encoding="utf-8")
    assert "unit or integration" in content, (
        'CI workflow must include a pytest -m "unit or integration" step.'
    )
