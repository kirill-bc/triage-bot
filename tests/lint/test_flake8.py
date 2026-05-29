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
            str(root / "src" / "triage_service" / "core" / "settings.py"),
            str(root / "src" / "triage_service" / "adapters" / "jira_action_executor.py"),
            str(root / "src" / "triage_service" / "adapters" / "jira_issue_fetcher.py"),
            str(root / "src" / "triage_service" / "adapters" / "jira_jql_search.py"),
            str(root / "src" / "triage_service" / "adapters" / "image_context_extractor.py"),
            str(root / "src" / "triage_service" / "adapters" / "jira_http_retry.py"),
            str(root / "src" / "triage_service" / "adapters" / "openrouter_inference_client.py"),
            str(root / "src" / "triage_service" / "core" / "policy_context.py"),
            str(root / "src" / "triage_service" / "core" / "issue_text_block.py"),
            str(root / "src" / "triage_service" / "core" / "prompt_composer.py"),
            str(root / "src" / "triage_service" / "core" / "vision_prompt_composer.py"),
            str(root / "src" / "triage_service" / "api" / "triage_api.py"),
            str(root / "src" / "triage_service" / "core" / "triage_fallback.py"),
            str(root / "src" / "triage_service" / "core" / "triage_handler.py"),
            str(root / "src" / "triage_service" / "core" / "triage_mismatch.py"),
            str(root / "triage_manual_cli.py"),
            str(root / "triage_bulk_cli.py"),
            str(root / "src" / "triage_service" / "core" / "triage_recommendation_parser.py"),
            str(root / "src" / "triage_service" / "observability" / "audit_events.py"),
            str(root / "src" / "triage_service" / "observability" / "audit_store.py"),
            str(
                root
                / "src"
                / "triage_service"
                / "observability"
                / "langfuse_inference_tracing.py",
            ),
            str(
                root
                / "src"
                / "triage_service"
                / "observability"
                / "langfuse_audit_store.py",
            ),
            str(root / "src" / "triage_service" / "observability" / "observability_wiring.py"),
            str(root / "src" / "triage_service" / "observability" / "payload_redaction.py"),
            str(root / "src" / "triage_service" / "observability" / "log_payload_guard.py"),
            str(root / "src" / "triage_service" / "observability" / "runtime_logging.py"),
            str(
                root
                / "src"
                / "triage_service"
                / "observability"
                / "structured_logger_audit_store.py",
            ),
            str(root / "dev_tunnel.py"),
            str(root / "scripts" / "fetch_jira_issue.py"),
            str(root / "scripts" / "fetch_jira_issue_image_context.py"),
            str(root / "scripts" / "run_triage_cli.py"),
            str(root / "scripts" / "run_bulk_triage_cli.py"),
            str(root / "scripts" / "run_dev_tunnel.py"),
            str(root / "scripts" / "benchmark" / "build_benchmark_dataset.py"),
            str(root / "scripts" / "benchmark" / "classification_benchmark.py"),
            str(root / "scripts" / "benchmark" / "benchmark_summary.py"),
            str(root / "scripts" / "benchmark" / "run_classification_benchmark.py"),
            str(root / "scripts" / "benchmark" / "summarize_benchmark_rows.py"),
            str(root / "tests" / "unit" / "test_image_context_wiring.py"),
            str(root / "tests" / "unit" / "test_image_context_observability.py"),
            str(root / "tests"),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
