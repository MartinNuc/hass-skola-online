from datetime import date, datetime, timedelta
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

from custom_components.skola_online.api.models import Entry
from custom_components.skola_online.calendar import (
    SkolaOnlineCalendar,
    to_calendar_event,
)

PRAGUE = ZoneInfo("Europe/Prague")


def _entry(hour: int, subject: str = "ČJ", is_lesson: bool = True) -> Entry:
    start = datetime(2026, 9, 14, hour, 0, tzinfo=PRAGUE)
    return Entry(
        start=start,
        end=start + timedelta(minutes=45),
        subject=subject,
        subject_full="Český jazyk a literatura" if is_lesson else None,
        teacher="Novák J." if is_lesson else None,
        room="U101" if is_lesson else None,
        period=1 if is_lesson else None,
        is_lesson=is_lesson,
    )


def _entry_between(start: datetime, end: datetime, subject: str) -> Entry:
    return Entry(
        start=start,
        end=end,
        subject=subject,
        subject_full=None,
        teacher=None,
        room=None,
        period=None,
        is_lesson=False,
    )


def _calendar_with_data(coordinator_data) -> SkolaOnlineCalendar:
    coordinator = MagicMock()
    coordinator.data = coordinator_data
    entry = MagicMock()
    entry.entry_id = "abc"
    entry.title = "Dítě Jedno"
    return SkolaOnlineCalendar(coordinator, entry)


def _calendar(entries: list[Entry]) -> SkolaOnlineCalendar:
    return _calendar_with_data({date(2026, 9, 14): entries})


def test_lesson_becomes_an_event_with_room_and_teacher():
    event = to_calendar_event(_entry(8))

    assert event.summary == "ČJ"
    assert event.location == "U101"
    assert "Český jazyk a literatura" in event.description
    assert "Novák J." in event.description


def test_school_event_carries_no_empty_description():
    event = to_calendar_event(_entry(8, subject="2. školní den", is_lesson=False))

    assert event.summary == "2. školní den"
    assert event.description is None
    assert event.location is None


async def test_get_events_filters_to_the_requested_window(hass):
    calendar = _calendar([_entry(8), _entry(10), _entry(13)])

    events = await calendar.async_get_events(
        hass,
        datetime(2026, 9, 14, 9, 0, tzinfo=PRAGUE),
        datetime(2026, 9, 14, 12, 0, tzinfo=PRAGUE),
    )

    assert [e.summary for e in events] == ["ČJ"]
    assert events[0].start == datetime(2026, 9, 14, 10, 0, tzinfo=PRAGUE)


def test_event_property_returns_the_next_upcoming_entry(freezer):
    freezer.move_to("2026-09-14T09:30:00+02:00")
    calendar = _calendar([_entry(8), _entry(10), _entry(13)])

    assert calendar.event.start == datetime(2026, 9, 14, 10, 0, tzinfo=PRAGUE)


def test_event_property_is_none_once_the_day_is_over(freezer):
    freezer.move_to("2026-09-14T20:00:00+02:00")
    calendar = _calendar([_entry(8)])

    assert calendar.event is None


def test_event_property_returns_a_lesson_already_in_progress(freezer):
    # 08:20 falls strictly inside the 08:00-08:45 lesson. A "next entry
    # whose start is in the future" implementation would skip straight to
    # the 10:00 entry and miss the lesson actually happening right now.
    freezer.move_to("2026-09-14T08:20:00+02:00")
    calendar = _calendar([_entry(8), _entry(10), _entry(13)])

    assert calendar.event.start == datetime(2026, 9, 14, 8, 0, tzinfo=PRAGUE)


async def test_get_events_excludes_entries_exactly_at_the_boundary(hass):
    start_date = datetime(2026, 9, 14, 9, 0, tzinfo=PRAGUE)
    end_date = datetime(2026, 9, 14, 12, 0, tzinfo=PRAGUE)

    ends_at_window_start = _entry_between(
        datetime(2026, 9, 14, 8, 15, tzinfo=PRAGUE), start_date, "ends-at-start"
    )
    starts_at_window_end = _entry_between(
        end_date, datetime(2026, 9, 14, 12, 45, tzinfo=PRAGUE), "starts-at-end"
    )
    straddles_the_start_boundary = _entry_between(
        datetime(2026, 9, 14, 8, 30, tzinfo=PRAGUE),
        datetime(2026, 9, 14, 9, 15, tzinfo=PRAGUE),
        "straddles-start",
    )
    calendar = _calendar(
        [ends_at_window_start, starts_at_window_end, straddles_the_start_boundary]
    )

    events = await calendar.async_get_events(hass, start_date, end_date)

    assert [e.summary for e in events] == ["straddles-start"]


async def test_no_data_yet_behaves_like_an_empty_timetable(hass):
    # coordinator.data is None before the first successful refresh.
    calendar = _calendar_with_data(None)

    assert calendar.event is None
    events = await calendar.async_get_events(
        hass,
        datetime(2026, 9, 14, 0, 0, tzinfo=PRAGUE),
        datetime(2026, 9, 15, 0, 0, tzinfo=PRAGUE),
    )
    assert events == []


async def test_entries_come_back_in_chronological_order_across_weeks(hass):
    # Two week keys supplied out of chronological order: the coordinator
    # dict is keyed by Monday, but dict iteration order follows insertion,
    # not date order, so the flattening must sort explicitly.
    earlier_monday = date(2026, 9, 14)
    later_monday = date(2026, 9, 21)
    later_entry = _entry_between(
        datetime(2026, 9, 21, 8, 0, tzinfo=PRAGUE),
        datetime(2026, 9, 21, 8, 45, tzinfo=PRAGUE),
        "later",
    )
    earlier_entry = _entry(8)  # 2026-09-14 08:00, subject "ČJ"
    calendar = _calendar_with_data(
        {later_monday: [later_entry], earlier_monday: [earlier_entry]}
    )

    events = await calendar.async_get_events(
        hass,
        datetime(2026, 9, 14, 0, 0, tzinfo=PRAGUE),
        datetime(2026, 9, 22, 0, 0, tzinfo=PRAGUE),
    )

    assert [e.summary for e in events] == ["ČJ", "later"]
