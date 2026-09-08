from datetime import date, timedelta
from unittest.mock import AsyncMock

import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.skola_online.api.exceptions import (
    CannotConnect,
    InvalidAuth,
    SessionExpired,
)
from custom_components.skola_online.const import (
    CONF_SCAN_INTERVAL_HOURS,
    CONF_WEEKS_AHEAD,
    DEFAULT_SCAN_INTERVAL_HOURS,
    DEFAULT_WEEKS_AHEAD,
    DOMAIN,
)
from custom_components.skola_online.coordinator import (
    SkolaOnlineCoordinator,
    mondays_to_fetch,
)


def test_mondays_to_fetch_starts_at_the_current_weeks_monday():
    # 2026-09-06 is a Sunday: the current week still starts on 31 August.
    assert mondays_to_fetch(date(2026, 9, 6), weeks=4) == [
        date(2026, 8, 31),
        date(2026, 9, 7),
        date(2026, 9, 14),
        date(2026, 9, 21),
    ]


def test_mondays_to_fetch_on_a_monday_starts_that_day():
    assert mondays_to_fetch(date(2026, 9, 14), weeks=2) == [
        date(2026, 9, 14),
        date(2026, 9, 21),
    ]


async def test_update_keys_entries_by_week(hass):
    client = AsyncMock()
    client.fetch_week.side_effect = lambda monday, child_id=None: [f"lesson-{monday}"]

    coordinator = SkolaOnlineCoordinator(hass, client, child_id="S001#D1")
    data = await coordinator._async_update_data()

    assert len(data) == 4
    assert client.fetch_week.await_count == 4
    for monday, entries in data.items():
        assert entries == [f"lesson-{monday}"]


async def test_invalid_auth_asks_home_assistant_to_reauthenticate(hass):
    client = AsyncMock()
    client.fetch_week.side_effect = InvalidAuth("nope")

    coordinator = SkolaOnlineCoordinator(hass, client, child_id=None)

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()


async def test_the_client_logs_in_once_and_stays_logged_in(hass):
    client = AsyncMock()
    client.fetch_week.return_value = []

    coordinator = SkolaOnlineCoordinator(hass, client, child_id=None)
    await coordinator._async_update_data()
    await coordinator._async_update_data()

    assert client.login.await_count == 1


async def test_a_rejected_password_forces_a_fresh_login_next_time(hass):
    client = AsyncMock()
    client.fetch_week.side_effect = InvalidAuth("nope")

    coordinator = SkolaOnlineCoordinator(hass, client, child_id=None)
    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()

    client.fetch_week.side_effect = None
    client.fetch_week.return_value = []
    await coordinator._async_update_data()

    assert client.login.await_count == 2


async def test_connection_trouble_keeps_the_previous_data(hass):
    client = AsyncMock()
    client.fetch_week.side_effect = CannotConnect("down")

    coordinator = SkolaOnlineCoordinator(hass, client, child_id=None)

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


async def test_config_entry_is_forwarded_to_the_base_coordinator(hass):
    client = AsyncMock()
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)

    coordinator = SkolaOnlineCoordinator(hass, client, child_id=None, config_entry=entry)

    assert coordinator.config_entry is entry


async def test_config_entry_defaults_to_none(hass):
    client = AsyncMock()

    coordinator = SkolaOnlineCoordinator(hass, client, child_id=None)

    assert coordinator.config_entry is None


async def test_a_rejected_password_at_first_login_asks_for_reauthentication(hass):
    """The setup-time login must reach reauth, not the generic handler.

    A user who changes their Škola Online password and then restarts Home
    Assistant hits this path — _authenticated is False again, so the very
    first login fails. Untranslated, it would only ever produce backoff.
    """
    client = AsyncMock()
    client.login.side_effect = InvalidAuth("nope")
    client.fetch_week.return_value = []

    coordinator = SkolaOnlineCoordinator(hass, client, child_id=None)

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()

    assert client.fetch_week.await_count == 0


async def test_a_rejected_first_login_is_retried_on_the_next_cycle(hass):
    client = AsyncMock()
    client.login.side_effect = InvalidAuth("nope")

    coordinator = SkolaOnlineCoordinator(hass, client, child_id=None)
    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()

    client.login.side_effect = None
    client.fetch_week.return_value = []
    await coordinator._async_update_data()

    assert client.login.await_count == 2


async def test_connection_trouble_during_the_first_login_is_an_update_failure(hass):
    client = AsyncMock()
    client.login.side_effect = CannotConnect("down")

    coordinator = SkolaOnlineCoordinator(hass, client, child_id=None)

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


async def test_an_expired_session_during_the_first_login_is_an_update_failure(hass):
    client = AsyncMock()
    client.login.side_effect = SessionExpired("bounced")

    coordinator = SkolaOnlineCoordinator(hass, client, child_id=None)

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


async def test_an_expired_session_is_an_update_failure_not_a_stack_trace(hass):
    """fetch_week re-logs-in once; if the retry is bounced too, this surfaces."""
    client = AsyncMock()
    client.fetch_week.side_effect = SessionExpired("redirected to www")

    coordinator = SkolaOnlineCoordinator(hass, client, child_id=None)

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


async def test_the_reauth_flow_actually_starts_when_setup_login_is_rejected(hass):
    """End to end: a bad password at setup must open a reauth flow.

    The bug this guards against was invisible to every test that only set
    fetch_week.side_effect, because login was an AsyncMock that never raised.
    """
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)

    client = AsyncMock()
    client.login.side_effect = InvalidAuth("nope")

    coordinator = SkolaOnlineCoordinator(hass, client, child_id=None, config_entry=entry)
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert [
        flow
        for flow in hass.config_entries.flow.async_progress()
        if flow["context"].get("source") == "reauth"
    ]


async def test_coordinator_uses_the_entrys_stored_options(hass):
    """Options over constants: an entry with explicit options is honoured."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        options={CONF_SCAN_INTERVAL_HOURS: 2, CONF_WEEKS_AHEAD: 6},
    )
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.fetch_week.return_value = []

    coordinator = SkolaOnlineCoordinator(hass, client, child_id=None, config_entry=entry)
    data = await coordinator._async_update_data()

    assert coordinator.update_interval == timedelta(hours=2)
    assert coordinator.weeks_ahead == 6
    assert len(data) == 6


async def test_coordinator_falls_back_to_defaults_with_no_options(hass):
    """Every entry created before this feature existed has no options."""
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.fetch_week.return_value = []

    coordinator = SkolaOnlineCoordinator(hass, client, child_id=None, config_entry=entry)
    data = await coordinator._async_update_data()

    assert coordinator.update_interval == timedelta(hours=DEFAULT_SCAN_INTERVAL_HOURS)
    assert coordinator.weeks_ahead == DEFAULT_WEEKS_AHEAD
    assert len(data) == DEFAULT_WEEKS_AHEAD


async def test_coordinator_falls_back_to_defaults_with_no_config_entry_at_all(hass):
    """The positional (hass, client, child_id) contract must keep working."""
    client = AsyncMock()
    client.fetch_week.return_value = []

    coordinator = SkolaOnlineCoordinator(hass, client, child_id=None)

    assert coordinator.update_interval == timedelta(hours=DEFAULT_SCAN_INTERVAL_HOURS)
    assert coordinator.weeks_ahead == DEFAULT_WEEKS_AHEAD
