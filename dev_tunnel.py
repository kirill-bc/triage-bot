"""Load ``.env``, run uvicorn, and attach a public tunnel (ngrok or cloudflared).

Intended for local development when Jira Automation must reach ``POST /triage``.
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv

TunnelKind = Literal["ngrok", "cloudflared"]

_REPO_ROOT = Path(__file__).resolve().parent


def build_uvicorn_argv(*, python_exe: str, host: str, port: int) -> list[str]:
    """Command line to run the FastAPI app with uvicorn."""
    return [
        python_exe,
        "-m",
        "uvicorn",
        "triage_service.api.triage_api:app",
        "--app-dir",
        "src",
        "--host",
        host,
        "--port",
        str(port),
    ]


def build_tunnel_argv(*, kind: TunnelKind, port: int) -> list[str]:
    """Command line for the selected tunnel CLI (must exist on ``PATH``)."""
    if kind == "ngrok":
        return ["ngrok", "http", str(port)]
    return ["cloudflared", "tunnel", "--url", f"http://127.0.0.1:{port}"]


def _terminate(proc: subprocess.Popen[str] | None, *, timeout: float = 5.0) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Load repo .env into the environment, start uvicorn on --host/--port, "
            "then run a tunnel so Jira can reach POST /triage."
        ),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Local port for uvicorn (default: 8000).",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Bind address for uvicorn (default: 0.0.0.0 for tunnel access).",
    )
    parser.add_argument(
        "--tunnel",
        choices=("ngrok", "cloudflared"),
        default="ngrok",
        help="Tunnel implementation (default: ngrok).",
    )
    parser.add_argument(
        "--no-inbound-log",
        action="store_true",
        help="Do not set TRIAGE_DEBUG_INBOUND (disable raw POST /triage body logging on stderr).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    env_path = _REPO_ROOT / ".env"
    if env_path.is_file():
        load_dotenv(env_path, override=False)
    else:
        print(
            f"Warning: no {env_path} found; starting with current environment only.",
            file=sys.stderr,
        )

    kind: TunnelKind = args.tunnel
    tunnel_argv = build_tunnel_argv(kind=kind, port=args.port)
    tunnel_bin = tunnel_argv[0]
    if shutil.which(tunnel_bin) is None:
        print(
            f"Error: '{tunnel_bin}' not found on PATH. Install it or choose the other "
            "tunnel via --tunnel.",
            file=sys.stderr,
        )
        return 1

    cwd = str(_REPO_ROOT)
    uvicorn_argv = build_uvicorn_argv(
        python_exe=sys.executable,
        host=args.host,
        port=args.port,
    )
    uvicorn_proc: subprocess.Popen[str] | None = None
    tunnel_proc: subprocess.Popen[str] | None = None

    def _forward_signal(signum: int, _frame: object | None) -> None:
        if tunnel_proc is not None and tunnel_proc.poll() is None:
            tunnel_proc.send_signal(signum)
        elif uvicorn_proc is not None and uvicorn_proc.poll() is None:
            uvicorn_proc.send_signal(signum)

    previous_int = signal.signal(signal.SIGINT, _forward_signal)
    previous_term = signal.signal(signal.SIGTERM, _forward_signal)

    child_env = dict(os.environ)
    if not args.no_inbound_log:
        child_env["TRIAGE_DEBUG_INBOUND"] = "1"
        print(
            "TRIAGE_DEBUG_INBOUND=1: each POST /triage logs the raw body to stderr "
            "before validation (use --no-inbound-log to disable).",
            file=sys.stderr,
        )

    try:
        uvicorn_proc = subprocess.Popen(
            uvicorn_argv,
            cwd=cwd,
            text=True,
            env=child_env,
        )
        time.sleep(0.25)
        if uvicorn_proc.poll() is not None:
            print("Error: uvicorn exited immediately; check logs above.", file=sys.stderr)
            return 1

        tunnel_proc = subprocess.Popen(tunnel_argv, cwd=cwd, text=True)
        tunnel_proc.wait()
        return tunnel_proc.returncode if tunnel_proc.returncode is not None else 1
    except KeyboardInterrupt:
        return 0
    finally:
        signal.signal(signal.SIGINT, previous_int)
        signal.signal(signal.SIGTERM, previous_term)
        _terminate(tunnel_proc)
        _terminate(uvicorn_proc)


if __name__ == "__main__":
    raise SystemExit(main())
