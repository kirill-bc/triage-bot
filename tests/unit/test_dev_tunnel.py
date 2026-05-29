"""Unit tests for ``dev_tunnel`` argv builders (local server + tunnel helper)."""

from __future__ import annotations

import pytest

from dev_tunnel import TunnelKind, build_tunnel_argv, build_uvicorn_argv


@pytest.mark.unit
def test_build_uvicorn_argv_includes_module_host_port() -> None:
    argv = build_uvicorn_argv(python_exe="/usr/bin/python3", host="0.0.0.0", port=8000)
    assert argv == [
        "/usr/bin/python3",
        "-m",
        "uvicorn",
        "triage_service.api.triage_api:app",
        "--app-dir",
        "src",
        "--host",
        "0.0.0.0",
        "--port",
        "8000",
        "--no-access-log",
    ]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("ngrok", ["ngrok", "http", "9000"]),
        ("cloudflared", ["cloudflared", "tunnel", "--url", "http://127.0.0.1:9000"]),
    ],
)
def test_build_tunnel_argv(kind: TunnelKind, expected: list[str]) -> None:
    assert build_tunnel_argv(kind=kind, port=9000) == expected
