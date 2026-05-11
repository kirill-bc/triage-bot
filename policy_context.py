"""Load bug and priority policy text bundled under ``policy/`` for triage prompts.

Replace the Markdown files with your organization's definitions:

- ``policy/bug_definition.md`` — when an issue should be classified as Bug vs Story
- ``policy/priority_definition.md`` — meaning of P0–P4 for this program

Future Confluence retrieval can populate the same ``PolicyContext`` shape.
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


def load_policy_context(*, policy_dir: Path | None = None) -> PolicyContext:
    """Read ``bug_definition.md`` and ``priority_definition.md`` from ``policy_dir``.

    If ``policy_dir`` is omitted, uses the ``policy/`` directory next to this module.
    """
    base = policy_dir if policy_dir is not None else Path(__file__).resolve().parent / "policy"
    paths = {
        "bug_definition": base / "bug_definition.md",
        "priority_definition": base / "priority_definition.md",
    }
    texts: dict[str, str] = {}
    for key, path in paths.items():
        if not path.is_file():
            raise PolicyContextLoadError(
                f"Missing policy file for {key}: {path} (expected UTF-8 Markdown)",
            )
        try:
            texts[key] = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise PolicyContextLoadError(f"Cannot read policy file {path}") from exc
    return PolicyContext(
        bug_definition=texts["bug_definition"],
        priority_definition=texts["priority_definition"],
    )
