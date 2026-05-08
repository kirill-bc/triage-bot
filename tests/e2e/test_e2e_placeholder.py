"""E2E marker present so `pytest -m e2e` collects tests; scenarios added with user flows."""

import pytest


@pytest.mark.e2e
def test_e2e_scenarios_not_yet_wired() -> None:
    pytest.skip("Define flows in docs/user_flows/index.md and replace this placeholder.")
