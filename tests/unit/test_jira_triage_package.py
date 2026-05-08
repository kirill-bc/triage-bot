"""Contract: installable package exposes version for tooling and imports."""

import importlib

import pytest


@pytest.mark.unit
def test_jira_triage_package_importable() -> None:
    mod = importlib.import_module("jira_triage")
    assert hasattr(mod, "__version__")


@pytest.mark.unit
def test_jira_triage_version_is_nonempty_string() -> None:
    mod = importlib.import_module("jira_triage")
    assert isinstance(mod.__version__, str)
    assert len(mod.__version__) > 0
