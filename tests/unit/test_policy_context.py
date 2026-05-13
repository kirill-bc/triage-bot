"""Policy context: bundled bug and priority definition text for triage prompts."""

from pathlib import Path

import pytest

from triage_service.core.policy_context import PolicyContextLoadError, load_policy_context


@pytest.mark.unit
def test_load_policy_context_reads_bug_and_priority_from_policy_dir(
    tmp_path: Path,
) -> None:
    policy_dir = tmp_path / "policy"
    policy_dir.mkdir()
    (policy_dir / "bug_definition.md").write_text("Bug rules here.\n", encoding="utf-8")
    (policy_dir / "priority_definition.md").write_text(
        "Priority ladder here.\n", encoding="utf-8",
    )
    ctx = load_policy_context(policy_dir=policy_dir)
    assert ctx.bug_definition == "Bug rules here."
    assert ctx.priority_definition == "Priority ladder here."


@pytest.mark.unit
def test_load_policy_context_strips_surrounding_whitespace(tmp_path: Path) -> None:
    policy_dir = tmp_path / "p"
    policy_dir.mkdir()
    (policy_dir / "bug_definition.md").write_text("  \nbody\n  ", encoding="utf-8")
    (policy_dir / "priority_definition.md").write_text("  x  ", encoding="utf-8")
    ctx = load_policy_context(policy_dir=policy_dir)
    assert ctx.bug_definition == "body"
    assert ctx.priority_definition == "x"


@pytest.mark.unit
def test_load_policy_context_default_uses_bundled_policy_files() -> None:
    ctx = load_policy_context()
    assert "Bug" in ctx.bug_definition
    assert "P0" in ctx.priority_definition


@pytest.mark.unit
def test_load_policy_context_raises_when_bug_file_missing(tmp_path: Path) -> None:
    policy_dir = tmp_path / "p"
    policy_dir.mkdir()
    (policy_dir / "priority_definition.md").write_text("ok", encoding="utf-8")
    with pytest.raises(PolicyContextLoadError, match="bug_definition"):
        load_policy_context(policy_dir=policy_dir)


@pytest.mark.unit
def test_load_policy_context_raises_when_priority_file_missing(tmp_path: Path) -> None:
    policy_dir = tmp_path / "p"
    policy_dir.mkdir()
    (policy_dir / "bug_definition.md").write_text("ok", encoding="utf-8")
    with pytest.raises(PolicyContextLoadError, match="priority_definition"):
        load_policy_context(policy_dir=policy_dir)
