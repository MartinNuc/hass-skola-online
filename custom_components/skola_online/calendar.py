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
from .const import (
    CONF_EVENT_TITLE,
    DEFAULT_EVENT_TITLE,
    DOMAIN,
    EVENT_TITLE_ABBREVIATION,
    EVENT_TITLE_BOTH,
    EVENT_TITLE_FULL,
)
from .coordinator import SkolaOnlineCoordinator


def _title(entry: Entry, title_style: str) -> str:
    """The event title for the chosen style.

    Never empty or "None": a lesson with no known full name (or a school
    event, which never has one) falls back to the abbreviation under every
    style.
    """
    if title_style == EVENT_TITLE_FULL:
        return entry.subject_full or entry.subject
    if title_style == EVENT_TITLE_BOTH:
        if entry.subject_full:
            return f"{entry.subject} — {entry.subject_full}"
        return entry.subject
    return entry.subject


def to_calendar_event(
    entry: Entry, title_style: str = EVENT_TITLE_ABBREVIATION
) -> CalendarEvent:
    """Map one timetable entry onto a calendar event.

    title_style picks what the summary shows (see const.EVENT_TITLE_*); it
    defaults to the abbreviation, which is what this function did before the
    option existed. The description always carries the full subject name and
    teacher, regardless of title_style, so no information is lost whichever
    title is picked - except when that would just repeat the title with
    nothing else, which is suppressed rather than shown as a redundant
    description.
    """
    summary = _title(entry, title_style)

    details = [part for part in (entry.subject_full, entry.teacher) if part]
    description = " — ".join(details) if details else None
    if description == summary:
        description = None

    return CalendarEvent(
        start=entry.start,
        end=entry.end,
        summary=summary,
        description=description,
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
        # Read straight from the ConfigEntry we were handed, not
        # coordinator.config_entry: it is the same object (the coordinator is
        # always constructed with config_entry=entry), but going through it
        # would be an indirection through the coordinator's own wiring for no
        # benefit, for a value the coordinator itself has no use for. Read
        # once here rather than on every event: OptionsFlowWithReload
        # reconstructs this entity on every options save, so a stale value
        # can't outlive the option that set it.
        self._title_style = entry.options.get(CONF_EVENT_TITLE, DEFAULT_EVENT_TITLE)

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
                return to_calendar_event(entry, self._title_style)
        return None

    async def async_get_events(
        self,
        hass: HomeAssistant,
        start_date: datetime,
        end_date: datetime,
    ) -> list[CalendarEvent]:
        """Filter the cached timetable. No I/O — paging the card is free."""
        return [
            to_calendar_event(entry, self._title_style)
            for entry in self._entries()
            if entry.start < end_date and entry.end > start_date
        ]
