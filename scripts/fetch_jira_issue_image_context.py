#!/usr/bin/env python3
"""Fetch one Jira issue and run vision attachment preprocessing (smoke / debug).

Requires Jira credentials and OpenRouter (for vision). Honors ``TRIAGE_IMAGE_CONTEXT_*``
settings from ``.env``; use ``--force`` to run vision when ``TRIAGE_IMAGE_CONTEXT_ENABLED``
is false.

Example (from repository root)::

    .venv/bin/python scripts/fetch_jira_issue_image_context.py TJC-123
    .venv/bin/python scripts/fetch_jira_issue_image_context.py TJC-123 --force
    .venv/bin/python scripts/fetch_jira_issue_image_context.py TJC-123 --show-issue-block
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

from pydantic import ValidationError

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from triage_service.adapters.image_context_extractor import (  # noqa: E402
    ImageContextExtractor,
    NoOpImageContextExtractor,
    OpenRouterVisionImageContextExtractor,
    build_image_context_extractor,
)
from triage_service.adapters.jira_issue_fetcher import (  # noqa: E402
    JiraIssueFetchError,
    JiraIssueFetcher,
)
from triage_service.adapters.openrouter_inference_client import (  # noqa: E402
    OpenRouterInferenceClient,
)
from triage_service.core.prompt_composer import _issue_block  # noqa: E402
from triage_service.core.settings import AppSettings, load_settings  # noqa: E402


def _build_extractor(
    settings: AppSettings,
    fetcher: JiraIssueFetcher,
    *,
    force: bool,
) -> ImageContextExtractor:
    if settings.triage_image_context_enabled:
        return build_image_context_extractor(settings, fetcher)
    if not force:
        return NoOpImageContextExtractor()
    vision_client = OpenRouterInferenceClient(
        settings,
        model_override=settings.triage_vision_model,
        http_timeout_seconds=settings.triage_image_context_timeout_seconds,
    )
    return OpenRouterVisionImageContextExtractor(
        settings=settings,
        jira_fetcher=fetcher,
        inference_client=vision_client,
        max_attachments=settings.triage_image_context_max_attachments,
        max_bytes_per_image=settings.triage_image_context_max_bytes_per_image,
    )


def _summarize_context(ctx: object) -> dict[str, object]:
    from triage_service.adapters.image_context_extractor import ImageContext

    if not isinstance(ctx, ImageContext):
        return {}
    item: dict[str, object] = {
        "attachment_id": ctx.attachment_id,
        "filename": ctx.filename,
    }
    if ctx.extraction_failure:
        item["status"] = "failed"
        item["extraction_failure"] = ctx.extraction_failure
    else:
        item["status"] = "ok"
        transcript = ctx.transcript or ""
        summary = ctx.summary or ""
        item["transcript_preview"] = transcript[:200] + ("…" if len(transcript) > 200 else "")
        item["summary"] = summary
    return item


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch a Jira issue, download image attachments, and run vision preprocessing."
        ),
    )
    parser.add_argument("issue_key", help="Jira issue key, e.g. TJC-123")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Run vision extraction even when TRIAGE_IMAGE_CONTEXT_ENABLED is false.",
    )
    parser.add_argument(
        "--show-issue-block",
        action="store_true",
        help="Print the Attached images section as it would appear in triage prompts.",
    )
    args = parser.parse_args()

    dotenv_path = _ROOT / ".env"
    try:
        settings = load_settings(
            env_file=dotenv_path if dotenv_path.is_file() else None,
        )
    except ValidationError as exc:
        print(f"Settings error: {exc}", file=sys.stderr)
        return 2

    if not settings.triage_image_context_enabled and not args.force:
        print(
            "TRIAGE_IMAGE_CONTEXT_ENABLED is false. Set it in .env or pass --force.",
            file=sys.stderr,
        )
        return 2

    run_id = str(uuid.uuid4())
    fetcher = JiraIssueFetcher(settings)
    try:
        issue = fetcher.fetch(args.issue_key.strip(), run_id=run_id)
    except JiraIssueFetchError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    extractor = _build_extractor(settings, fetcher, force=args.force)
    if isinstance(extractor, NoOpImageContextExtractor):
        print("Image context extraction is disabled (NoOp extractor).", file=sys.stderr)
        return 2

    vision_model = settings.triage_vision_model
    if isinstance(extractor, OpenRouterVisionImageContextExtractor):
        vision_model = extractor._inference.effective_model_id

    extraction = extractor.extract(issue, run_id=run_id)
    contexts = extraction.contexts
    ok_count = sum(1 for c in contexts if not c.extraction_failure)
    fail_count = len(contexts) - ok_count

    payload: dict[str, object] = {
        "issue_key": issue.issue_key,
        "run_id": run_id,
        "vision_model": vision_model,
        "attachments_on_issue": len(issue.attachments),
        "attachments_considered": extraction.attachments_considered,
        "attachments_extracted": extraction.attachments_extracted,
        "total_bytes": extraction.total_bytes,
        "total_vision_cost": extraction.total_vision_cost,
        "image_attachments_processed": len(contexts),
        "extracted_ok": ok_count,
        "extracted_failed": fail_count,
        "attachments": [_summarize_context(c) for c in contexts],
        "contexts": [c.model_dump() for c in contexts],
    }

    print(json.dumps(payload, indent=2, ensure_ascii=False))

    if args.show_issue_block:
        print("\n--- issue_block (attached images section) ---\n")
        print(_issue_block(issue, image_contexts=contexts))

    return 0 if fail_count == 0 or ok_count > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
