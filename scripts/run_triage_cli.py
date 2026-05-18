#!/usr/bin/env python3
"""Run full triage for one issue from the CLI (``source=manual_trigger``).

Example (from repository root)::

    .venv/bin/python scripts/run_triage_cli.py TJC-123
    .venv/bin/python scripts/run_triage_cli.py TJC-123 --project TJC
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

from triage_manual_cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
