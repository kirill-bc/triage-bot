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
            str(root / "jira_action_executor.py"),
            str(root / "jira_issue_fetcher.py"),
            str(root / "jira_rest_paths.py"),
            str(root / "openrouter_inference_client.py"),
            str(root / "policy_context.py"),
            str(root / "prompt_composer.py"),
            str(root / "triage_api.py"),
            str(root / "triage_fallback.py"),
            str(root / "triage_handler.py"),
            str(root / "triage_mismatch.py"),
            str(root / "triage_manual_cli.py"),
            str(root / "triage_recommendation_parser.py"),
            str(root / "classification_benchmark.py"),
            str(root / "benchmark_summary.py"),
            str(root / "dev_tunnel.py"),
            str(root / "scripts" / "fetch_jira_issue.py"),
            str(root / "scripts" / "run_triage_cli.py"),
            str(root / "scripts" / "run_dev_tunnel.py"),
            str(root / "scripts" / "benchmark" / "build_benchmark_dataset.py"),
            str(root / "scripts" / "benchmark" / "run_classification_benchmark.py"),
            str(root / "scripts" / "benchmark" / "summarize_benchmark_rows.py"),
            str(root / "tests"),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
