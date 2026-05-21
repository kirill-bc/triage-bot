#!/usr/bin/env python3
"""Run triage for all issues matched by JQL and write a JSON report (no Jira comments by default).

Example (from repository root)::

    .venv/bin/python scripts/run_bulk_triage_cli.py \\
        --jql 'project = TJC AND issuetype = Bug AND created >= -7d' \\
        -o /tmp/bulk_triage.json

    .venv/bin/python scripts/run_bulk_triage_cli.py \\
        --jql 'project = TJC AND labels = triage-candidate' \\
        --max-results 10 \\
        -o bulk_report.json
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

from triage_bulk_cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
