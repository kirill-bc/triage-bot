#!/usr/bin/env python3
"""Start uvicorn plus ngrok or cloudflared using repo ``.env``.

Example (from repository root)::

    .venv/bin/python scripts/run_dev_tunnel.py
    .venv/bin/python scripts/run_dev_tunnel.py --tunnel cloudflared --port 8080
    .venv/bin/python scripts/run_dev_tunnel.py --no-inbound-log
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dev_tunnel import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
