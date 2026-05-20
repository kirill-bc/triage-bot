"""Unit tests for prompt_composer: split classification vs priority inputs."""

import importlib
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from triage_service.core.settings import AppSettings
from triage_service.adapters.image_context_extractor import ImageContext
from triage_service.adapters.jira_issue_fetcher import FetchedIssue
from triage_service.core.policy_context import PolicyContext

from triage_service.core.prompt_composer import (
    _issue_block,
    compose_classification_prompt,
    compose_classification_system_prompt,
    compose_priority_prompt,
    compose_priority_system_prompt,
)


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> AppSettings:
    monkeypatch.setenv("JIRA_API_KEY", "jira-api-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-token")
    monkeypatch.setenv("TRIAGE_WEBHOOK_TOKEN", "triage-token")
    return AppSettings()


@pytest.mark.unit
def test_classification_prompt_includes_bug_policy_and_issue_excludes_priority_text(
    settings: AppSettings,
) -> None:
    policy = PolicyContext(
        bug_definition="BUGPOLICY_UNIQUE_ALPHA",
        priority_definition="PRIORITYPOLICY_UNIQUE_BETA",
    )
    issue = FetchedIssue(
        issue_key="TJC-1",
        summary="Login fails",
        description="Steps to reproduce",
        reproduction_steps="1) Open app\n2) Click login",
        issue_type="Bug",
        priority="P2",
        reporter="support@example.com",
    )
    text = compose_classification_prompt(policy, issue, settings=settings)
    assert "BUGPOLICY_UNIQUE_ALPHA" in text
    assert "TJC-1" in text and "Login fails" in text
    assert "PRIORITYPOLICY_UNIQUE_BETA" not in text
    assert "## Task" in text
    assert "## Bug definition (policy)" in text
    assert "TriageBot" not in text
    assert "## Role" not in text
    assert "JSON field `reason`" in text
    assert "Reproduction steps" in text
    assert "1) Open app" in text


@pytest.mark.unit
def test_priority_prompt_includes_priority_policy_and_issue_excludes_bug_text(
    settings: AppSettings,
) -> None:
    policy = PolicyContext(
        bug_definition="BUGPOLICY_UNIQUE_GAMMA",
        priority_definition="PRIORITYPOLICY_UNIQUE_DELTA",
    )
    issue = FetchedIssue(
        issue_key="BC-99",
        summary="Crash on save",
        description=None,
        issue_type="Story",
        priority=None,
        reporter="Jane Doe",
    )
    text = compose_priority_prompt(policy, issue, settings=settings)
    assert "PRIORITYPOLICY_UNIQUE_DELTA" in text
    assert "BC-99" in text and "Crash on save" in text
    assert "BUGPOLICY_UNIQUE_GAMMA" not in text
    assert "## Task" in text
    assert "## Priority definition (policy)" in text
    assert "TriageBot" not in text
    assert "## Role" not in text
    assert "JSON field `reason`" in text


@pytest.mark.unit
def test_issue_block_omits_attached_images_when_no_contexts(
    settings: AppSettings,
) -> None:
    issue = FetchedIssue(
        issue_key="TJC-10",
        summary="Login error",
        description="Fails on submit",
        issue_type="Bug",
        priority="P2",
        reporter="alice",
    )
    block = _issue_block(issue)
    assert "Attached images" not in block
    assert "Reproduction steps:" in block


@pytest.mark.unit
def test_issue_block_renders_attached_images_summary_without_transcript(
    settings: AppSettings,
) -> None:
    issue = FetchedIssue(
        issue_key="TJC-11",
        summary="UI broken",
        description="See screenshot",
        issue_type="Bug",
        priority="P1",
        reporter="bob",
    )
    contexts = [
        ImageContext(
            attachment_id="10001",
            filename="toast.png",
            transcript="Error: connection refused",
            summary="Red toast on the login form.",
        ),
    ]
    block = _issue_block(issue, image_contexts=contexts)
    assert "Attached images:" in block
    assert "[Attachment 1: toast.png]" in block
    assert "Transcript:" not in block
    assert "connection refused" not in block
    assert "Summary:\nRed toast on the login form." in block


@pytest.mark.unit
def test_issue_block_renders_soft_failure_placeholder_for_extraction_errors(
    settings: AppSettings,
) -> None:
    issue = FetchedIssue(
        issue_key="TJC-12",
        summary="Crash",
        issue_type="Bug",
        reporter="carol",
    )
    contexts = [
        ImageContext(
            attachment_id="10002",
            filename="huge.png",
            extraction_failure="exceeds size limit",
        ),
    ]
    block = _issue_block(issue, image_contexts=contexts)
    assert "[Attachment 1: extraction unavailable — exceeds size limit]" in block
    assert "Transcript:" not in block


@pytest.mark.unit
def test_classification_prompt_includes_attached_images_from_image_contexts(
    settings: AppSettings,
) -> None:
    policy = PolicyContext(bug_definition="BUG", priority_definition="PRI")
    issue = FetchedIssue(
        issue_key="TJC-13",
        summary="Blank screen",
        issue_type="Bug",
        reporter="dave",
    )
    contexts = [
        ImageContext(
            attachment_id="9",
            filename="screen.png",
            transcript="404 Not Found",
            summary="White page with 404 heading.",
        ),
    ]
    text = compose_classification_prompt(policy, issue, settings=settings, image_contexts=contexts)
    assert "Attached images:" in text
    assert "Summary:\nWhite page with 404 heading." in text
    assert "Transcript:" not in text


@pytest.mark.unit
def test_classification_prompt_shows_placeholder_when_description_and_priority_missing(
    settings: AppSettings,
) -> None:
    policy = PolicyContext(bug_definition="x", priority_definition="y")
    issue = FetchedIssue(
        issue_key="K-1",
        summary="S",
        description=None,
        reproduction_steps=None,
        issue_type="Bug",
        priority=None,
        reporter="r",
    )
    text = compose_classification_prompt(policy, issue, settings=settings)
    assert "Current Jira priority: (none)" in text
    assert "Description:\n(none)" in text
    assert "Reproduction steps:\n(none)" in text


@pytest.mark.unit
def test_prompt_composer_loads_templates_from_external_json_path(
    tmp_path: Path, settings: AppSettings,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    template_path = tmp_path / "prompt_templates.json"
    template_path.write_text(
        json.dumps(
            {
                "reason_for_humans": "CUSTOM_REASON_BLOCK\n\n",
                "classification_system_prompt": "CLASSIFICATION SYSTEM",
                "priority_system_prompt": "PRIORITY SYSTEM",
                "classification_template": (
                    "CLASSIFY PREFIX\n"
                    "{reason_for_humans}"
                    "BUG POLICY:\n{bug_definition}\n\n"
                    "ISSUE DATA:\n{issue_block}\n"
                ),
                "priority_template": (
                    "PRIORITY PREFIX\n"
                    "{reason_for_humans}"
                    "PRIORITY POLICY:\n{priority_definition}\n\n"
                    "ISSUE DATA:\n{issue_block}\n"
                ),
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("TRIAGE_PROMPT_TEMPLATES_PATH", str(template_path))
    module = importlib.reload(sys.modules["triage_service.core.prompt_composer"])
    policy = PolicyContext(bug_definition="BUG_RULE", priority_definition="P_RULE")
    issue = FetchedIssue(
        issue_key="TJC-2",
        summary="Something broke",
        description="Detailed description",
        issue_type="Bug",
        priority="P1",
        reporter="agent@example.com",
    )

    classification_text = module.compose_classification_prompt(policy, issue, settings=settings)
    priority_text = module.compose_priority_prompt(policy, issue, settings=settings)
    classification_system = module.compose_classification_system_prompt(settings=settings)
    priority_system = module.compose_priority_system_prompt(settings=settings)

    assert classification_text.startswith("CLASSIFY PREFIX")
    assert "CUSTOM_REASON_BLOCK" in classification_text
    assert "BUG POLICY:\nBUG_RULE" in classification_text
    assert priority_text.startswith("PRIORITY PREFIX")
    assert "CUSTOM_REASON_BLOCK" in priority_text
    assert "PRIORITY POLICY:\nP_RULE" in priority_text
    assert classification_system == "CLASSIFICATION SYSTEM"
    assert priority_system == "PRIORITY SYSTEM"


@pytest.mark.unit
def test_system_prompts_fall_back_to_local_templates_when_langfuse_not_configured(
    settings: AppSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.delenv("TRIAGE_PROMPT_TEMPLATES_PATH", raising=False)
    module = importlib.reload(sys.modules["triage_service.core.prompt_composer"])
    classification_system = module.compose_classification_system_prompt(settings=settings)
    priority_system = module.compose_priority_system_prompt(settings=settings)
    assert "single JSON object only" in classification_system
    assert "recommended_issue_type" in classification_system
    assert "TriageBot" in classification_system
    assert "recommended_priority" in priority_system
    assert "TriageBot" in priority_system
    assert "still" in priority_system


@pytest.mark.unit
def test_system_prompts_use_langfuse_when_configured(
    settings: AppSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    monkeypatch.setenv(
        "TRIAGE_LANGFUSE_CLASSIFICATION_SYSTEM_PROMPT_NAME",
        "triagebot/classification-system",
    )
    monkeypatch.setenv(
        "TRIAGE_LANGFUSE_PRIORITY_SYSTEM_PROMPT_NAME",
        "triagebot/priority-system",
    )

    fake_classification_system = MagicMock()
    fake_classification_system.compile.return_value = "LF CLASSIFICATION SYSTEM"
    fake_priority_system = MagicMock()
    fake_priority_system.compile.return_value = "LF PRIORITY SYSTEM"
    fake_client = MagicMock()

    def _get_prompt(name: str, **kwargs: object) -> MagicMock:
        _ = kwargs
        if name == "triagebot/classification-system":
            return fake_classification_system
        if name == "triagebot/priority-system":
            return fake_priority_system
        raise AssertionError(f"unexpected prompt name: {name}")

    fake_client.get_prompt.side_effect = _get_prompt
    monkeypatch.setattr(
        "triage_service.core.langfuse_prompt_config.get_client",
        lambda: fake_client,
    )
    settings = AppSettings()

    assert compose_classification_system_prompt(settings=settings) == "LF CLASSIFICATION SYSTEM"
    assert compose_priority_system_prompt(settings=settings) == "LF PRIORITY SYSTEM"


@pytest.mark.unit
def test_reason_for_humans_uses_langfuse_when_configured(
    settings: AppSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    monkeypatch.setenv(
        "TRIAGE_LANGFUSE_REASON_FOR_HUMANS_PROMPT_NAME",
        "triagebot/reason-for-humans",
    )

    fake_reason = MagicMock()
    fake_reason.compile.return_value = "LANGFUSE REASON BLOCK\n\n"
    fake_client = MagicMock()

    def _get_prompt(name: str, **kwargs: object) -> MagicMock:
        _ = kwargs
        if name == "triagebot/reason-for-humans":
            return fake_reason
        if name == "triagebot/classification-user":
            raise RuntimeError("force local classification template")
        raise AssertionError(f"unexpected prompt name: {name}")

    fake_client.get_prompt.side_effect = _get_prompt
    monkeypatch.setattr(
        "triage_service.core.langfuse_prompt_config.get_client",
        lambda: fake_client,
    )
    settings = AppSettings()

    policy = PolicyContext(bug_definition="BUG_RULE", priority_definition="P_RULE")
    issue = FetchedIssue(
        issue_key="TJC-77",
        summary="Broken flow",
        description="details",
        issue_type="Bug",
        priority="P2",
        reporter="agent@example.com",
    )

    text = compose_classification_prompt(policy, issue, settings=settings)

    assert "LANGFUSE REASON BLOCK" in text
    assert "BUG_RULE" in text
    fake_reason.compile.assert_called_once_with()


@pytest.mark.unit
def test_classification_prompt_uses_langfuse_prompt_template_when_configured(
    settings: AppSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    monkeypatch.setenv(
        "TRIAGE_LANGFUSE_CLASSIFICATION_PROMPT_NAME",
        "triagebot/classification-user",
    )
    monkeypatch.setenv("TRIAGE_LANGFUSE_PROMPT_LABEL", "staging")
    monkeypatch.setenv("TRIAGE_LANGFUSE_PROMPT_CACHE_TTL_SECONDS", "120")

    fake_classification = MagicMock()
    fake_classification.compile.return_value = "compiled-from-langfuse"
    fake_client = MagicMock()

    def _get_prompt(name: str, **kwargs: object) -> MagicMock:
        _ = kwargs
        if name == "triagebot/classification-user":
            return fake_classification
        raise AssertionError(f"unexpected prompt name: {name}")

    fake_client.get_prompt.side_effect = _get_prompt
    monkeypatch.setattr(
        "triage_service.core.langfuse_prompt_config.get_client",
        lambda: fake_client,
    )
    settings = AppSettings()

    policy = PolicyContext(bug_definition="unused", priority_definition="unused")
    issue = FetchedIssue(
        issue_key="TJC-321",
        summary="Customer cannot save settings",
        description="Save button no-op",
        reproduction_steps="Open settings, click save",
        issue_type="Bug",
        priority="P2",
        reporter="triage@example.com",
    )

    prompt = compose_classification_prompt(policy, issue, settings=settings)

    assert prompt == "compiled-from-langfuse"
    fake_client.get_prompt.assert_called_once()
    compile_kwargs = fake_classification.compile.call_args.kwargs
    assert set(compile_kwargs) == {"issue_block"}
    assert "TJC-321" in compile_kwargs["issue_block"]
    assert "Customer cannot save settings" in compile_kwargs["issue_block"]


@pytest.mark.unit
def test_priority_prompt_uses_langfuse_with_issue_block_only(
    settings: AppSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")

    fake_priority = MagicMock()
    fake_priority.compile.return_value = "priority-from-langfuse"
    fake_client = MagicMock()
    fake_client.get_prompt.return_value = fake_priority
    monkeypatch.setattr(
        "triage_service.core.langfuse_prompt_config.get_client",
        lambda: fake_client,
    )
    settings = AppSettings()

    policy = PolicyContext(bug_definition="unused", priority_definition="unused")
    issue = FetchedIssue(
        issue_key="TJC-400",
        summary="Outage",
        description="all down",
        issue_type="Bug",
        priority="P0",
        reporter="ops@example.com",
    )

    prompt = compose_priority_prompt(policy, issue, settings=settings)

    assert prompt == "priority-from-langfuse"
    compile_kwargs = fake_priority.compile.call_args.kwargs
    assert set(compile_kwargs) == {"issue_block"}
    assert "TJC-400" in compile_kwargs["issue_block"]


@pytest.mark.unit
def test_priority_prompt_falls_back_to_local_templates_when_langfuse_fetch_fails(
    settings: AppSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    monkeypatch.setenv("TRIAGE_LANGFUSE_PRIORITY_PROMPT_NAME", "triagebot/priority-user")

    fake_client = MagicMock()
    fake_client.get_prompt.side_effect = RuntimeError("langfuse unavailable")
    monkeypatch.setattr(
        "triage_service.core.langfuse_prompt_config.get_client",
        lambda: fake_client,
    )

    policy = PolicyContext(bug_definition="unused", priority_definition="P_DEF_FALLBACK")
    issue = FetchedIssue(
        issue_key="TJC-322",
        summary="App crashes",
        description="crash details",
        issue_type="Bug",
        priority="P1",
        reporter="triage@example.com",
    )

    prompt = compose_priority_prompt(policy, issue, settings=settings)

    assert "P_DEF_FALLBACK" in prompt
    assert "TJC-322" in prompt
