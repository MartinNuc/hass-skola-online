"""Exposes a child's timetable as a calendar entity."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import SkolaOnlineConfigEntry
from .api.models import Entry
from .const import DOMAIN
from .coordinator import SkolaOnlineCoordinator


def to_calendar_event(entry: Entry) -> CalendarEvent:
    """Map one timetable entry onto a calendar event.

    The summary is the abbreviation, which is what fits in a calendar card
    cell; the full subject name and teacher go in the description.
    """
    details = [part for part in (entry.subject_full, entry.teacher) if part]

    return CalendarEvent(
        start=entry.start,
        end=entry.end,
        summary=entry.subject,
        description=" — ".join(details) if details else None,
        location=entry.room,
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SkolaOnlineConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the calendar entity for this config entry."""
    async_add_entities([SkolaOnlineCalendar(entry.runtime_data, entry)])


class SkolaOnlineCalendar(CoordinatorEntity[SkolaOnlineCoordinator], CalendarEntity):
    """A child's school timetable."""

    _attr_has_entity_name = True
    _attr_name = None

    def __init__(
        self, coordinator: SkolaOnlineCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = entry.entry_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Škola Online",
        )

    def _entries(self) -> list[Entry]:
        """Every cached entry, oldest first."""
        weeks = self.coordinator.data or {}
        entries = [entry for week in weeks.values() for entry in week]
        entries.sort(key=lambda item: item.start)
        return entries

    @property
    def event(self) -> CalendarEvent | None:
        """The lesson happening now, or the next one due."""
        now = dt_util.now()
        for entry in self._entries():
            if entry.end > now:
                return to_calendar_event(entry)
        return None

    async def async_get_events(
        self,
        hass: HomeAssistant,
        start_date: datetime,
        end_date: datetime,
    ) -> list[CalendarEvent]:
        """Filter the cached timetable. No I/O — paging the card is free."""
        return [
            to_calendar_event(entry)
            for entry in self._entries()
            if entry.start < end_date and entry.end > start_date
        ]
