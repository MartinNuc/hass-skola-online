from datetime import date, datetime
from zoneinfo import ZoneInfo

from custom_components.skola_online.api.parser import parse_tooltip, parse_week

from .fixtures.grid import EMPTY_CELL, day_row, lesson_cell, week_html

PRAGUE = ZoneInfo("Europe/Prague")


def test_parse_tooltip_splits_title_and_labelled_values():
    attr = (
        "onMouseOverTooltip('ČJ (Český jazyk a literatura) ',"
        "'Učitel:~Novák J.~Třída:~9.A~Učebna:~U101"
        "~Den (vyuč. hodina):~Po 14.9. (1)')"
    )
    parsed = parse_tooltip(attr)

    assert parsed["abbrev"] == "ČJ"
    assert parsed["full"] == "Český jazyk a literatura"
    assert parsed["Učitel"] == "Novák J."
    assert parsed["Učebna"] == "U101"


def test_parse_week_builds_an_entry_with_local_times():
    html = week_html(
        rows=day_row(
            "Po",
            "14.9.",
            EMPTY_CELL
            + lesson_cell("ČJ", "Český jazyk a literatura", "Novák J.", "U101", "Po 14.9.", 1)
            + EMPTY_CELL * 2,
        )
    )
    entries = parse_week(html, monday=date(2026, 9, 14), tz=PRAGUE)

    assert len(entries) == 1
    entry = entries[0]
    assert entry.start == datetime(2026, 9, 14, 8, 30, tzinfo=PRAGUE)
    assert entry.end == datetime(2026, 9, 14, 9, 15, tzinfo=PRAGUE)
    assert entry.subject == "ČJ"
    assert entry.subject_full == "Český jazyk a literatura"
    assert entry.teacher == "Novák J."
    assert entry.room == "U101"
    assert entry.period == 1
    assert entry.is_lesson is True


def test_parse_week_returns_entries_sorted_by_start():
    html = week_html(
        rows=day_row(
            "Út",
            "15.9.",
            EMPTY_CELL
            + lesson_cell("M", "Matematika", "Novák J.", "U101", "Út 15.9.", 1)
            + lesson_cell("AJ", "Anglický jazyk", "Dvořák P.", "L209", "Út 15.9.", 2)
            + EMPTY_CELL,
        )
        + day_row(
            "Po",
            "14.9.",
            EMPTY_CELL * 3
            + lesson_cell("TV", "Tělesná výchova", "Novák J.", "T1", "Po 14.9.", 3),
        )
    )
    entries = parse_week(html, monday=date(2026, 9, 14), tz=PRAGUE)

    assert [e.subject for e in entries] == ["TV", "M", "AJ"]


def test_parse_week_keeps_a_tooltip_period_of_zero():
    """A tooltip-reported period of 0 must survive, not fall back to the header.

    The live site numbers its first period 0 (07:40-08:25). Header periods
    here are numbered 5-8 to guarantee the header-derived Period.number (5)
    genuinely differs from the tooltip's period (0), so a truthiness-based
    fallback (`... or first.number`) would silently overwrite 0 with 5.
    """
    html = week_html(
        rows=day_row(
            "Po",
            "14.9.",
            lesson_cell("ČJ", "Český jazyk a literatura", "Novák J.", "U101", "Po 14.9.", 0)
            + EMPTY_CELL * 3,
        ),
        periods=[
            (5, "07:40", "08:25"),
            (6, "08:30", "09:15"),
            (7, "09:25", "10:10"),
            (8, "10:30", "11:15"),
        ],
    )
    entries = parse_week(html, monday=date(2026, 9, 14), tz=PRAGUE)

    assert len(entries) == 1
    assert entries[0].period == 0


def test_parse_week_falls_back_to_visible_text_when_a_tooltip_is_missing():
    cell = (
        '<td class="DctCellBottom DctCell">'
        '<table class="DctInnerTableType10">'
        '<tr><td><div class="KuvBunkaRozvrhNadpis">HV</div>'
        '<div class="KuvBunkaRozvrhText">9.A U101</div>'
        "</td></tr></table></td>"
    )
    html = week_html(rows=day_row("Po", "14.9.", EMPTY_CELL + cell + EMPTY_CELL * 2))
    entries = parse_week(html, monday=date(2026, 9, 14), tz=PRAGUE)

    assert len(entries) == 1
    assert entries[0].subject == "HV"
    assert entries[0].subject_full is None
    assert entries[0].teacher is None
    assert entries[0].is_lesson is True
