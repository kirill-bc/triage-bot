"""Integration marker smoke: collection and environment run under integration gate."""

import pytest


@pytest.mark.integration
def test_pytest_integration_marker_collects() -> None:
    assert True
