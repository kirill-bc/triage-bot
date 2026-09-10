#!/usr/bin/env python3
"""Triage one issue and POST the rendered outcome to Jira Automation.

Configure ``JIRA_AUTOMATION_WEBHOOK_URL`` and
``JIRA_AUTOMATION_WEBHOOK_TOKEN`` in ``.env`` or the process environment.

Example (from repository root)::

    .venv/bin/python scripts/run_webhook_triage_cli.py TJC-123
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from webhook_triage_cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
