from datetime import date, time
from zoneinfo import ZoneInfo

import pytest

from custom_components.skola_online.api.exceptions import ParseError
from custom_components.skola_online.api.parser import (
    parse_day_rows,
    parse_periods,
    parse_week,
)

from .fixtures.grid import EMPTY_CELL, day_row, lesson_cell, week_html

PRAGUE = ZoneInfo("Europe/Prague")


def test_parse_periods_reads_number_and_clock_times():
    html = week_html(rows="")
    periods = parse_periods(html)

    assert periods[0].number == 0
    assert periods[0].start == time(7, 40)
    assert periods[0].end == time(8, 25)
    assert periods[3].number == 3
    assert periods[3].end == time(11, 15)


def test_parse_periods_rejects_a_page_without_the_grid():
    with pytest.raises(ParseError):
        parse_periods("<html><body>Přihlášení</body></html>")


def test_parse_day_rows_derives_dates_from_the_requested_monday():
    html = week_html(
        rows=day_row("Po", "14.9.", EMPTY_CELL * 4)
        + day_row("St", "16.9.", EMPTY_CELL * 4)
    )
    days = parse_day_rows(html, monday=date(2026, 9, 14))

    assert days == {1: date(2026, 9, 14), 2: date(2026, 9, 16)}


def test_parse_day_rows_handles_a_week_spanning_new_year():
    """Day labels carry no year, so the year comes from the requested Monday."""
    html = week_html(
        rows=day_row("Po", "29.12.", EMPTY_CELL * 4)
        + day_row("Čt", "1.1.", EMPTY_CELL * 4)
    )
    days = parse_day_rows(html, monday=date(2025, 12, 29))

    assert days == {1: date(2025, 12, 29), 2: date(2026, 1, 1)}


def test_parse_periods_keys_by_column_not_by_printed_number():
    """Dict keys are column indexes, not the printed period numbers.

    This test protects against a regression where parse_periods might be
    refactored to use the printed number as the key instead of the column index.
    Task 3's column arithmetic depends on keys being 0-based column positions.
    """
    # Periods numbered 5, 6, 7, 8 sitting at columns 0, 1, 2, 3
    html = week_html(
        rows="",
        periods=[(5, "07:40", "08:25"), (6, "08:30", "09:15"), (7, "09:25", "10:10"), (8, "10:30", "11:15")]
    )
    periods = parse_periods(html)

    # Keys are column indexes 0, 1, 2, 3
    assert set(periods.keys()) == {0, 1, 2, 3}
    # Period numbers are 5, 6, 7, 8
    assert periods[0].number == 5
    assert periods[1].number == 6
    assert periods[2].number == 7
    assert periods[3].number == 8


def _with_tbody(html: str) -> str:
    """Wrap the grid's rows in <tbody>, as an ASP.NET grid control may."""
    open_tag = '<table id="CCADynamicCalendarTable" class="DctTable">'
    assert open_tag in html
    return html.replace(open_tag, open_tag + "<tbody>").replace(
        "</table></body>", "</tbody></table></body>"
    )


def test_the_grid_is_read_the_same_through_a_tbody_wrapper():
    """A live Infragistics grid may emit <tbody>; html.parser never does.

    Without this, find_all("tr", recursive=False) on the table finds nothing
    and every single fetch raises ParseError - the integration would never
    produce an event again.
    """
    rows = day_row(
        "Po",
        "14.9.",
        EMPTY_CELL
        + lesson_cell("ČJ", "Český jazyk", "Novák J.", "U101", "Po 14.9.", 1)
        + EMPTY_CELL * 2,
    )
    plain = week_html(rows=rows)
    wrapped = _with_tbody(plain)

    assert "<tbody>" in wrapped

    assert parse_periods(wrapped) == parse_periods(plain)
    assert parse_day_rows(wrapped, monday=date(2026, 9, 14)) == parse_day_rows(
        plain, monday=date(2026, 9, 14)
    )
    assert parse_week(wrapped, monday=date(2026, 9, 14), tz=PRAGUE) == parse_week(
        plain, monday=date(2026, 9, 14), tz=PRAGUE
    )

    entries = parse_week(wrapped, monday=date(2026, 9, 14), tz=PRAGUE)
    assert len(entries) == 1
    assert entries[0].subject == "ČJ"
    assert entries[0].room == "U101"
