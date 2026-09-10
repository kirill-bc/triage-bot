"""Run one manual triage and deliver its outcome through Jira Automation."""

from __future__ import annotations

import os
import sys

import triage_manual_cli

_APPLY_MODE_ENV = "TRIAGE_JIRA_APPLY_MODE"
_AUTO_APPLY_FLAGS = (
    "--auto-apply-deescalation",
    "--auto-apply-escalation",
    "--auto-apply-bug-to-story",
)


def _argv_with_mutations_enabled(argv: list[str] | None) -> list[str]:
    """Webhook smoke runs always emit mutation directives unless the flag is already present."""
    args = list(argv) if argv is not None else sys.argv[1:]
    extra = [flag for flag in _AUTO_APPLY_FLAGS if flag not in args]
    return [*args, *extra]


def main(argv: list[str] | None = None) -> int:
    """Force callback delivery and delegate inference/output to the manual CLI."""
    previous_mode = os.environ.get(_APPLY_MODE_ENV)
    os.environ[_APPLY_MODE_ENV] = "automation_webhook"
    try:
        return triage_manual_cli.main(_argv_with_mutations_enabled(argv))
    finally:
        if previous_mode is None:
            os.environ.pop(_APPLY_MODE_ENV, None)
        else:
            os.environ[_APPLY_MODE_ENV] = previous_mode
