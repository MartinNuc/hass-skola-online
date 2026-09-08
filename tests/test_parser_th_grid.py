"""The live page uses <th> for the header row and each day's label cell;
these fixtures use <td> for lessons throughout, matching the real grid's
shape (see tests/fixtures/real_week.html). Parsing must be identical to the
all-<td> shape the other synthetic tests use - same periods, same dates,
same entries - including for a lesson sitting in the very first lesson
column, where an off-by-one would silently shift every period and time.
"""

from datetime import date
from zoneinfo import ZoneInfo

from custom_components.skola_online.api.parser import (
    parse_day_rows,
    parse_periods,
    parse_week,
)

from .fixtures.grid import EMPTY_CELL, day_row, lesson_cell, week_html

PRAGUE = ZoneInfo("Europe/Prague")


def test_parse_periods_matches_between_th_and_td_headers():
    plain = week_html(rows="")
    th_shaped = week_html(rows="", header_tag="th")

    assert parse_periods(th_shaped) == parse_periods(plain)
    # Sanity: this isn't vacuously true - periods were actually found.
    assert parse_periods(th_shaped)


def test_parse_day_rows_matches_between_th_and_td_labels():
    plain_rows = day_row("Po", "14.9.", EMPTY_CELL * 4) + day_row(
        "St", "16.9.", EMPTY_CELL * 4
    )
    th_rows = day_row("Po", "14.9.", EMPTY_CELL * 4, label_tag="th") + day_row(
        "St", "16.9.", EMPTY_CELL * 4, label_tag="th"
    )

    plain = week_html(rows=plain_rows)
    th_shaped = week_html(rows=th_rows, header_tag="th")

    days = parse_day_rows(th_shaped, monday=date(2026, 9, 14))
    assert days == parse_day_rows(plain, monday=date(2026, 9, 14))
    # Sanity: both day rows were actually found, not silently skipped.
    assert days == {1: date(2026, 9, 14), 2: date(2026, 9, 16)}


def test_parse_week_matches_between_th_and_td_shapes_including_first_column():
    """A lesson in the very first lesson column (column 0) is exactly what
    the off-by-one bug would corrupt: on the real page, dropping the day
    label's <th> from the cell search shifts every lesson one column to
    the left, so a lesson actually in column 0 would be read as if it were
    in column -1 (lost) and a lesson in column 1 would be misread as
    column 0's period and times.
    """
    first_column_lesson = lesson_cell(
        "ČJ", "Český jazyk a literatura", "Novák J.", "U101", "Po 14.9.", 0
    )
    second_column_lesson = lesson_cell(
        "MA", "Matematika", "Svoboda P.", "U202", "Po 14.9.", 1
    )

    plain_rows = day_row(
        "Po",
        "14.9.",
        first_column_lesson + second_column_lesson + EMPTY_CELL * 2,
    )
    th_rows = day_row(
        "Po",
        "14.9.",
        first_column_lesson + second_column_lesson + EMPTY_CELL * 2,
        label_tag="th",
    )

    plain = week_html(rows=plain_rows)
    th_shaped = week_html(rows=th_rows, header_tag="th")

    plain_entries = parse_week(plain, monday=date(2026, 9, 14), tz=PRAGUE)
    th_entries = parse_week(th_shaped, monday=date(2026, 9, 14), tz=PRAGUE)

    assert th_entries == plain_entries
    assert len(th_entries) == 2

    first = next(e for e in th_entries if e.subject == "ČJ")
    second = next(e for e in th_entries if e.subject == "MA")

    # The first lesson column's period (0) and times, unshifted.
    assert first.period == 0
    assert first.start.time().isoformat(timespec="minutes") == "07:40"
    assert first.end.time().isoformat(timespec="minutes") == "08:25"

    assert second.period == 1
    assert second.start.time().isoformat(timespec="minutes") == "08:30"
