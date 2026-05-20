"""Load bug and priority policy text for local prompt-template fallback.

When Langfuse user prompts are used, policies are embedded in ``classification-user`` /
``priority-user``; these Markdown files apply only when composing from ``prompt_templates.json``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class PolicyContextLoadError(RuntimeError):
    """Raised when policy definition files are missing or unreadable."""


@dataclass(frozen=True)
class PolicyContext:
    """Plain-text policy excerpts for model context."""

    bug_definition: str
    priority_definition: str


def _read_local_policy_text(path: Path, *, key: str) -> str:
    if not path.is_file():
        raise PolicyContextLoadError(
            f"Missing policy file for {key}: {path} (expected UTF-8 Markdown)",
        )
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise PolicyContextLoadError(f"Cannot read policy file {path}") from exc


def load_policy_context(*, policy_dir: Path | None = None) -> PolicyContext:
    """Load bug and priority policy text from bundled Markdown (local template fallback).

    If ``policy_dir`` is omitted, uses the ``policy/`` directory next to this module.
    """
    base = policy_dir if policy_dir is not None else Path(__file__).resolve().parent / "policy"
    bug_definition = _read_local_policy_text(base / "bug_definition.md", key="bug_definition")
    priority_definition = _read_local_policy_text(
        base / "priority_definition.md",
        key="priority_definition",
    )
    return PolicyContext(
        bug_definition=bug_definition,
        priority_definition=priority_definition,
    )
