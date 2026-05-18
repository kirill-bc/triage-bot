"""Local manual triage entry: infer project from issue key, run with ``source=manual_trigger``."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

from pydantic import ValidationError

from triage_service.core.triage_fallback import TriageFailure
from triage_service.core.triage_handler import TriageRunner, build_default_triage_handler
from triage_service.core.triage_recommendation_parser import TriageRecommendation

_ROOT = Path(__file__).resolve().parent


def infer_project_from_issue_key(issue_key: str) -> str:
    """Return the Jira project key embedded in a standard ``PROJ-123`` issue key."""
    stripped = issue_key.strip()
    if "-" not in stripped:
        msg = "Issue key must contain '-' followed by a numeric suffix, e.g. TJC-123."
        raise ValueError(msg)
    head, tail = stripped.rsplit("-", 1)
    if not tail.isdigit():
        msg = "Issue key suffix after the last '-' must be numeric (Jira issue number)."
        raise ValueError(msg)
    if not head:
        msg = "Issue key must include a non-empty project key before the issue number."
        raise ValueError(msg)
    return head


def run_cli_triage(
    issue_key: str,
    *,
    project: str | None = None,
    runner: TriageRunner | None = None,
) -> TriageRecommendation | TriageFailure:
    """Run synchronous triage with ``source="manual_trigger"`` (same pipeline as the webhook)."""
    key = issue_key.strip()
    proj = project.strip() if project is not None else infer_project_from_issue_key(key)
    resolved = runner if runner is not None else build_default_triage_handler()
    outcome = resolved.run_sync(key, proj, "manual_trigger", run_id=str(uuid.uuid4()))
    flush = getattr(resolved, "flush_inference_telemetry", None)
    if callable(flush):
        flush()
    return outcome


def main(argv: list[str] | None = None) -> int:
    """Parse CLI args, validate settings, run triage, print JSON outcome to stdout."""
    parser = argparse.ArgumentParser(
        description=(
            "Run full triage for one issue (source=manual_trigger; "
            "no Jira Automation required)."
        ),
    )
    parser.add_argument(
        "issue_key",
        help="Jira issue key, e.g. TJC-123",
    )
    parser.add_argument(
        "--project",
        default=None,
        help="Jira project key (default: inferred from issue key, e.g. TJC from TJC-123).",
    )
    ns = parser.parse_args(argv)

    dotenv_path = _ROOT / ".env"
    try:
        from triage_service.core.settings import load_settings

        load_settings(env_file=dotenv_path if dotenv_path.is_file() else None)
    except ValidationError as exc:
        print(f"Settings error: {exc}", file=sys.stderr)
        return 2

    project_arg = ns.project.strip() if ns.project else None
    outcome = run_cli_triage(ns.issue_key, project=project_arg)
    if isinstance(outcome, TriageFailure):
        print(json.dumps({"status": "failed", "failure": outcome.model_dump()}, indent=2))
        return 1
    print(
        json.dumps(
            {"status": "completed", "recommendation": outcome.model_dump()},
            indent=2,
            ensure_ascii=False,
        ),
    )
    return 0
