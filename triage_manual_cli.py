"""Local manual triage entry: infer project from issue key, run with ``source=manual_trigger``."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from triage_service.adapters.image_context_extractor import build_cli_image_context_summary
from triage_service.core.settings import AppSettings
from triage_service.core.triage_fallback import TriageFailure
from triage_service.core.triage_handler import (
    TriageRunner,
    TriageSyncResult,
    build_default_triage_handler,
)

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
    post_mismatch_comments: bool = True,
    apply_to_jira: bool = True,
    auto_apply_deescalation: bool | None = None,
    auto_apply_bug_to_story: bool | None = None,
) -> TriageSyncResult:
    """Run synchronous triage with ``source="manual_trigger"`` (same pipeline as the webhook)."""
    key = issue_key.strip()
    proj = project.strip() if project is not None else infer_project_from_issue_key(key)
    if runner is not None:
        resolved = runner
    else:
        resolved = build_default_triage_handler(
            post_mismatch_comments=post_mismatch_comments,
            apply_to_jira=apply_to_jira,
            auto_apply_deescalation=auto_apply_deescalation,
            auto_apply_bug_to_story=auto_apply_bug_to_story,
        )
    result = resolved.run_sync(key, proj, "manual_trigger", run_id=str(uuid.uuid4()))
    flush = getattr(resolved, "flush_inference_telemetry", None)
    if callable(flush):
        flush()
    return result


def build_triage_cli_result_payload(
    result: TriageSyncResult,
    *,
    image_context: dict[str, Any],
) -> dict[str, Any]:
    outcome = result.outcome
    assert not isinstance(outcome, TriageFailure)
    payload: dict[str, Any] = {
        "status": "completed",
        "recommendation": outcome.model_dump(),
        "image_context": image_context,
    }
    if result.classification is not None:
        payload["classification"] = result.classification.model_dump()
    if result.priority is not None:
        payload["priority"] = result.priority.model_dump()
    return payload


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
    parser.add_argument(
        "--no-comment",
        action="store_true",
        help=(
            "Apply triagebot labels but do not post mismatch comments to Jira "
            "(useful for dry-run debugging)."
        ),
    )
    parser.add_argument(
        "--auto-apply-deescalation",
        action="store_true",
        help=(
            "When writing to Jira, apply less-urgent priority recommendations directly "
            "instead of advisory-only comments."
        ),
    )
    parser.add_argument(
        "--auto-apply-bug-to-story",
        action="store_true",
        help=(
            "When writing to Jira, apply Bug -> Story recommendation directly "
            "instead of advisory-only comments."
        ),
    )
    ns = parser.parse_args(argv)

    dotenv_path = _ROOT / ".env"
    try:
        from triage_service.core.settings import load_settings

        settings: AppSettings = load_settings(
            env_file=dotenv_path if dotenv_path.is_file() else None,
        )
    except ValidationError as exc:
        print(f"Settings error: {exc}", file=sys.stderr)
        return 2

    project_arg = ns.project.strip() if ns.project else None
    result = run_cli_triage(
        ns.issue_key,
        project=project_arg,
        post_mismatch_comments=not ns.no_comment,
        auto_apply_deescalation=ns.auto_apply_deescalation,
        auto_apply_bug_to_story=ns.auto_apply_bug_to_story,
    )
    image_context = build_cli_image_context_summary(
        enabled=settings.triage_image_context_enabled,
        extraction=result.image_extraction,
    )
    outcome = result.outcome
    if isinstance(outcome, TriageFailure):
        print(
            json.dumps(
                {
                    "status": "failed",
                    "failure": outcome.model_dump(),
                    "image_context": image_context,
                },
                indent=2,
            ),
        )
        return 1
    print(
        json.dumps(
            build_triage_cli_result_payload(result, image_context=image_context),
            indent=2,
            ensure_ascii=False,
        ),
    )
    return 0
