#!/usr/bin/env python3
"""Smoke-fetch one Jira issue by key using repo settings (.env or environment).

Example (from repository root):

    .venv/bin/python scripts/fetch_jira_issue.py TJC-123
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from jira_issue_fetcher import JiraIssueFetcher, JiraIssueFetchError  # noqa: E402
from settings import load_settings  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch a Jira issue via REST and print normalized fields as JSON.",
    )
    parser.add_argument(
        "issue_key",
        help="Jira issue key, e.g. TJC-123",
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

    fetcher = JiraIssueFetcher(settings)
    try:
        issue = fetcher.fetch(args.issue_key.strip())
    except JiraIssueFetchError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(json.dumps(issue.model_dump(), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
