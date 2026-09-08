"""Fetches the timetable and hands it to the calendar entity."""

from __future__ import annotations

import logging
from datetime import date, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api.client import SkolaOnlineClient
from .api.exceptions import CannotConnect, InvalidAuth, ParseError, SessionExpired
from .api.models import Entry
from .const import (
    CONF_SCAN_INTERVAL_HOURS,
    CONF_WEEKS_AHEAD,
    DEFAULT_SCAN_INTERVAL_HOURS,
    DEFAULT_WEEKS_AHEAD,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


def mondays_to_fetch(today: date, weeks: int) -> list[date]:
    """The Monday of the current week, plus the following weeks' Mondays.

    "Current" means the week containing today, so a Sunday refresh still
    returns the week that is ending rather than the one starting tomorrow.
    """
    first = today - timedelta(days=today.weekday())
    return [first + timedelta(weeks=offset) for offset in range(weeks)]


class SkolaOnlineCoordinator(DataUpdateCoordinator[dict[date, list[Entry]]]):
    """Polls Škola Online for the next few weeks of timetable."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: SkolaOnlineClient,
        child_id: str | None,
        config_entry: ConfigEntry | None = None,
    ) -> None:
        # Options over constants: every entry created before the options flow
        # existed has none, so .get() with the old hardcoded defaults keeps
        # those entries working unchanged.
        options = config_entry.options if config_entry is not None else {}
        scan_interval_hours = options.get(
            CONF_SCAN_INTERVAL_HOURS, DEFAULT_SCAN_INTERVAL_HOURS
        )
        self.weeks_ahead = options.get(CONF_WEEKS_AHEAD, DEFAULT_WEEKS_AHEAD)

        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=timedelta(hours=scan_interval_hours),
        )
        self.client = client
        self.child_id = child_id
        self._authenticated = False

    async def _async_update_data(self) -> dict[date, list[Entry]]:
        """Fetch each week in turn.

        Sequentially, not concurrently: the weeks share one ASP.NET session and
        one viewstate, so overlapping requests would fight over server state.
        """
        if not self._authenticated:
            # This login has to translate its failures exactly as the week loop
            # does. Left untranslated, an InvalidAuth here reaches
            # DataUpdateCoordinator's generic handler, which logs a traceback
            # and never starts a reauth flow — so a password changed at the
            # portal would leave the entry retrying with backoff forever
            # instead of asking the user for the new one.
            try:
                await self.client.login()
            except InvalidAuth as err:
                self._authenticated = False
                raise ConfigEntryAuthFailed(
                    "Škola Online rejected the stored credentials"
                ) from err
            except (CannotConnect, SessionExpired) as err:
                raise UpdateFailed(f"could not log in: {err}") from err
            self._authenticated = True

        today = dt_util.now().date()
        weeks: dict[date, list[Entry]] = {}

        for monday in mondays_to_fetch(today, self.weeks_ahead):
            try:
                weeks[monday] = await self.client.fetch_week(
                    monday, child_id=self.child_id
                )
            except InvalidAuth as err:
                # Force a fresh login on the next attempt, so a password change
                # picked up by reauth takes effect without a reload.
                self._authenticated = False
                raise ConfigEntryAuthFailed(
                    "Škola Online rejected the stored credentials"
                ) from err
            except (CannotConnect, ParseError, SessionExpired) as err:
                raise UpdateFailed(f"could not read week of {monday}: {err}") from err

        return weeks
