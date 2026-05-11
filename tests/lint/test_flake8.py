"""Gate: flake8 on application and test trees."""

import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.lint
def test_flake8_passes_on_src_and_tests() -> None:
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "flake8",
            str(root / "settings.py"),
            str(root / "core_config.py"),
            str(root / "triage_api.py"),
            str(root / "tests"),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
