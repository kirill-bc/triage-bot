"""Unit tests for the package-oriented refactor scaffold."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.mark.unit
def test_target_package_layout_and_ownership_doc_exist() -> None:
    root = Path(__file__).resolve().parents[2]
    src_root = root / "src" / "triage_service"
    expected_packages = ("api", "core", "adapters", "observability")

    assert (src_root / "__init__.py").is_file()
    for package in expected_packages:
        assert (src_root / package / "__init__.py").is_file()

    overview = root / "docs" / "architecture" / "overview.md"
    assert overview.is_file()

    text = overview.read_text(encoding="utf-8").lower()
    for package in expected_packages:
        assert f"`{package}`" in text
    assert "ownership boundaries" in text


@pytest.mark.unit
def test_triage_api_module_lives_under_api_package() -> None:
    root = Path(__file__).resolve().parents[2]
    assert (root / "src" / "triage_service" / "api" / "triage_api.py").is_file()
    assert not (root / "triage_api.py").exists()


@pytest.mark.unit
def test_core_orchestration_modules_live_under_core_package() -> None:
    root = Path(__file__).resolve().parents[2]
    core_root = root / "src" / "triage_service" / "core"
    module_names = (
        "triage_handler.py",
        "triage_fallback.py",
        "triage_mismatch.py",
        "triage_recommendation_parser.py",
    )

    for name in module_names:
        assert (core_root / name).is_file()
        assert not (root / name).exists()


@pytest.mark.unit
def test_external_adapter_modules_live_under_adapters_package() -> None:
    root = Path(__file__).resolve().parents[2]
    adapters_root = root / "src" / "triage_service" / "adapters"
    module_names = (
        "jira_issue_fetcher.py",
        "jira_action_executor.py",
        "openrouter_inference_client.py",
    )

    for name in module_names:
        assert (adapters_root / name).is_file()
        assert not (root / name).exists()


@pytest.mark.unit
def test_prompt_builder_and_templates_live_under_core_package() -> None:
    root = Path(__file__).resolve().parents[2]
    core_root = root / "src" / "triage_service" / "core"
    assert (core_root / "prompt_composer.py").is_file()
    assert (core_root / "prompt_templates.json").is_file()
    assert not (root / "prompt_composer.py").exists()
    assert not (root / "prompt_templates.json").exists()


@pytest.mark.unit
def test_policy_context_and_policy_files_live_under_core_package() -> None:
    root = Path(__file__).resolve().parents[2]
    core_root = root / "src" / "triage_service" / "core"
    core_policy_root = core_root / "policy"

    assert (core_root / "policy_context.py").is_file()
    assert (core_policy_root / "bug_definition.md").is_file()
    assert (core_policy_root / "priority_definition.md").is_file()

    assert not (root / "policy_context.py").exists()
    assert not (root / "policy" / "bug_definition.md").exists()
    assert not (root / "policy" / "priority_definition.md").exists()


@pytest.mark.unit
def test_benchmark_modules_live_under_scripts_benchmark() -> None:
    root = Path(__file__).resolve().parents[2]
    benchmark_root = root / "scripts" / "benchmark"

    assert (benchmark_root / "classification_benchmark.py").is_file()
    assert (benchmark_root / "benchmark_summary.py").is_file()

    assert not (root / "classification_benchmark.py").exists()
    assert not (root / "benchmark_summary.py").exists()


@pytest.mark.unit
def test_core_allowlist_config_is_consolidated_into_settings_module() -> None:
    root = Path(__file__).resolve().parents[2]
    assert not (root / "core_config.py").exists()
    assert (root / "src" / "triage_service" / "core" / "settings.py").is_file()
    assert not (root / "settings.py").exists()
