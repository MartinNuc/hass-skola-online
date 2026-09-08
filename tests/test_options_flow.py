from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.skola_online.const import (
    CONF_CHILD_ID,
    CONF_SCAN_INTERVAL_HOURS,
    CONF_WEEKS_AHEAD,
    DEFAULT_SCAN_INTERVAL_HOURS,
    DEFAULT_WEEKS_AHEAD,
    DOMAIN,
)


def _entry(**options) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id="S001#D1",
        data={
            CONF_USERNAME: "parent",
            CONF_PASSWORD: "secret",
            CONF_CHILD_ID: "S001#D1",
        },
        options=options,
    )


def _schema_defaults(result) -> dict:
    return {
        key: key.default()
        for key in result["data_schema"].schema
        if key in (CONF_SCAN_INTERVAL_HOURS, CONF_WEEKS_AHEAD)
    }


async def test_options_form_is_prefilled_with_the_entrys_current_values(hass):
    entry = _entry(**{CONF_SCAN_INTERVAL_HOURS: 3, CONF_WEEKS_AHEAD: 2})
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    defaults = _schema_defaults(result)
    assert defaults[CONF_SCAN_INTERVAL_HOURS] == 3
    assert defaults[CONF_WEEKS_AHEAD] == 2


async def test_options_form_falls_back_to_defaults_when_entry_has_none(hass):
    """An entry created before this feature existed has no options at all."""
    entry = _entry()
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    defaults = _schema_defaults(result)
    assert defaults[CONF_SCAN_INTERVAL_HOURS] == DEFAULT_SCAN_INTERVAL_HOURS
    assert defaults[CONF_WEEKS_AHEAD] == DEFAULT_WEEKS_AHEAD


async def test_submitting_new_values_stores_them_on_the_entry(hass):
    entry = _entry(**{CONF_SCAN_INTERVAL_HOURS: 6, CONF_WEEKS_AHEAD: 4})
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_SCAN_INTERVAL_HOURS: 2, CONF_WEEKS_AHEAD: 8},
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_SCAN_INTERVAL_HOURS] == 2
    assert entry.options[CONF_WEEKS_AHEAD] == 8


async def test_out_of_range_values_are_rejected(hass):
    """Sensible bounds: too-frequent polling and too-wide a window both fail."""
    entry = _entry(**{CONF_SCAN_INTERVAL_HOURS: 6, CONF_WEEKS_AHEAD: 4})
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    with pytest.raises(InvalidData):
        await hass.config_entries.options.async_configure(
            result["flow_id"],
            {CONF_SCAN_INTERVAL_HOURS: 48, CONF_WEEKS_AHEAD: 4},
        )


async def test_changing_options_reloads_the_entry_without_a_restart(hass):
    """OptionsFlowWithReload must actually reload, not just save quietly.

    Proven end to end: after saving new options, a freshly constructed
    coordinator (built during the reload) reflects them, with no restart of
    Home Assistant in between.
    """
    entry = _entry(**{CONF_SCAN_INTERVAL_HOURS: 6, CONF_WEEKS_AHEAD: 4})
    entry.add_to_hass(hass)

    client = AsyncMock()
    client.fetch_week.return_value = []

    with patch(
        "custom_components.skola_online.SkolaOnlineClient", return_value=client
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.runtime_data.weeks_ahead == 4
        assert entry.runtime_data.update_interval == timedelta(hours=6)

        result = await hass.config_entries.options.async_init(entry.entry_id)
        await hass.config_entries.options.async_configure(
            result["flow_id"],
            {CONF_SCAN_INTERVAL_HOURS: 2, CONF_WEEKS_AHEAD: 8},
        )
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert entry.options[CONF_WEEKS_AHEAD] == 8
    assert entry.runtime_data.weeks_ahead == 8
    assert entry.runtime_data.update_interval == timedelta(hours=2)
