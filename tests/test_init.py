from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.skola_online.const import CONF_CHILD_ID, CONF_CHILD_NAME, DOMAIN


def _entry(child: str = "S001#D1", name: str = "Dítě Jedno") -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=child,
        title=name,
        data={
            CONF_USERNAME: "parent",
            CONF_PASSWORD: "secret",
            CONF_CHILD_ID: child,
            CONF_CHILD_NAME: name,
        },
    )


async def test_setup_creates_the_calendar_entity(hass):
    entry = _entry()
    entry.add_to_hass(hass)

    client = AsyncMock()
    client.fetch_week.return_value = []

    with patch(
        "custom_components.skola_online.SkolaOnlineClient", return_value=client
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get("calendar.dite_jedno") is not None


async def test_unload_removes_the_entity(hass):
    entry = _entry()
    entry.add_to_hass(hass)

    client = AsyncMock()
    client.fetch_week.return_value = []

    with patch(
        "custom_components.skola_online.SkolaOnlineClient", return_value=client
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_each_entry_gets_its_own_http_session(hass):
    """Siblings must not share a cookie jar.

    One jar means one ASP.NET session and one server-side viewstate between
    them, and their refreshes fire within a second of each other — so one
    entry's login would rotate the session out from under the other's fetch.
    """
    sessions = []

    def _make_client(username, password, session, tz):
        sessions.append(session)
        client = AsyncMock()
        client.fetch_week.return_value = []
        return client

    entries = [_entry(), _entry(child="S001#D2", name="Dítě Druhé")]
    for entry in entries:
        entry.add_to_hass(hass)

    with patch(
        "custom_components.skola_online.SkolaOnlineClient", side_effect=_make_client
    ):
        # Setting up the integration sets up every entry it owns.
        assert await hass.config_entries.async_setup(entries[0].entry_id)
        await hass.async_block_till_done()

    assert all(entry.state is ConfigEntryState.LOADED for entry in entries)
    assert len(sessions) == 2
    first, second = sessions
    assert first is not second
    assert first.cookie_jar is not second.cookie_jar
    # And neither is the session Home Assistant hands out to everyone.
    shared = async_get_clientsession(hass)
    assert first is not shared
    assert second is not shared


async def test_unloading_releases_the_entrys_session(hass):
    sessions = []

    def _make_client(username, password, session, tz):
        sessions.append(session)
        client = AsyncMock()
        client.fetch_week.return_value = []
        return client

    entry = _entry()
    entry.add_to_hass(hass)

    with patch(
        "custom_components.skola_online.SkolaOnlineClient", side_effect=_make_client
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert sessions[0].closed is False

        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

    assert sessions[0].closed is True
