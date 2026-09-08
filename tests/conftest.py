"""Shared fixtures. Enables custom integrations for the HA test harness."""
import pytest

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """HA refuses to load custom integrations in tests without this."""
    yield
