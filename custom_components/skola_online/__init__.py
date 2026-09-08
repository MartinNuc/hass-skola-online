"""The Škola Online integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.util import dt as dt_util

from .api.client import SkolaOnlineClient
from .const import CONF_CHILD_ID
from .coordinator import SkolaOnlineCoordinator

PLATFORMS: list[Platform] = [Platform.CALENDAR]

type SkolaOnlineConfigEntry = ConfigEntry[SkolaOnlineCoordinator]


async def async_setup_entry(
    hass: HomeAssistant, entry: SkolaOnlineConfigEntry
) -> bool:
    """Set up Škola Online from a config entry."""
    # A session of this entry's own, not the shared one: the school's server
    # keeps the selected week and the viewstate in one ASP.NET session, keyed
    # by cookie. Two entries sharing a cookie jar would share that session, so
    # a sibling's refresh — they fire within a second of each other — would
    # rotate the session out from under this one mid-fetch.
    session = async_create_clientsession(hass)
    # Detach, never close: the connector underneath is Home Assistant's own
    # shared one, so closing the session would take it down for everyone.
    # Registered before the first refresh, so a setup that never completes
    # releases its session too — HA runs the on-unload callbacks both when
    # setup fails and when a loaded entry unloads successfully.
    entry.async_on_unload(session.detach)
    client = SkolaOnlineClient(
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        session,
        dt_util.get_default_time_zone(),
    )
    coordinator = SkolaOnlineCoordinator(
        hass,
        client,
        entry.data.get(CONF_CHILD_ID),
        config_entry=entry,
    )

    # Raises ConfigEntryAuthFailed or ConfigEntryNotReady for us, so a bad
    # password sends the user to reauth instead of silently loading empty.
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: SkolaOnlineConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
