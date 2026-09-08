from datetime import date, datetime
from zoneinfo import ZoneInfo

from custom_components.skola_online.api.parser import (
    parse_children,
    parse_hidden_fields,
    parse_week,
)

from .fixtures.grid import EMPTY_CELL, day_row, lesson_cell, week_html

PRAGUE = ZoneInfo("Europe/Prague")


def _event_cell(title: str, colspan: int) -> str:
    """School events use a different inner-table type than lessons."""
    return (
        f'<td class="DctCellBottom DctCell" colspan="{colspan}">'
        '<table class="DctInnerTableType30">'
        f'<tr><td><div class="KuvBunkaRozvrhNadpis">{title}</div>'
        '<div class="KuvBunkaRozvrhText">I.A, I.B</div>'
        "</td></tr></table></td>"
    )


def test_school_event_is_flagged_and_spans_its_periods():
    html = week_html(
        rows=day_row("St", "16.9.", EMPTY_CELL + _event_cell("2. školní den", 3))
    )
    entries = parse_week(html, monday=date(2026, 9, 14), tz=PRAGUE)

    assert len(entries) == 1
    event = entries[0]
    assert event.is_lesson is False
    assert event.subject == "2. školní den"
    assert event.subject_full is None
    assert event.teacher is None
    # Spans periods 1..3: starts at period 1 and ends at period 3.
    assert event.start == datetime(2026, 9, 16, 8, 30, tzinfo=PRAGUE)
    assert event.end == datetime(2026, 9, 16, 11, 15, tzinfo=PRAGUE)


def test_a_malformed_cell_is_skipped_without_losing_the_week():
    broken = '<td class="DctCellBottom DctCell"><table class="DctInnerTableType10">'
    broken += "<tr><td></td></tr></table></td>"
    html = week_html(
        rows=day_row(
            "Po",
            "14.9.",
            EMPTY_CELL
            + broken
            + lesson_cell("M", "Matematika", "Novák J.", "U101", "Po 14.9.", 2)
            + EMPTY_CELL,
        )
    )
    entries = parse_week(html, monday=date(2026, 9, 14), tz=PRAGUE)

    assert [e.subject for e in entries] == ["M"]


def test_an_empty_week_yields_no_entries():
    """Holidays render a grid with day rows and nothing in them."""
    html = week_html(
        rows=day_row("Po", "14.9.", EMPTY_CELL * 4)
        + day_row("Út", "15.9.", EMPTY_CELL * 4)
    )

    assert parse_week(html, monday=date(2026, 9, 14), tz=PRAGUE) == []


def test_parse_children_reads_the_dropdown():
    html = (
        '<select name="listOfChildrenPart$listOfChildren$DDLChildren">'
        '<option value="">-- vyberte --</option>'
        '<option value="S001#Z000123">Dítě Jedno</option>'
        '<option value="S001#Z000125">   </option>'
        '<option value="S001#Z000124">Dítě Druhé</option>'
        "</select>"
    )
    children = parse_children(html)

    assert [(c.id, c.name) for c in children] == [
        ("S001#Z000123", "Dítě Jedno"),
        ("S001#Z000124", "Dítě Druhé"),
    ]


def test_parse_hidden_fields_collects_the_webforms_state():
    html = (
        '<input type="hidden" name="__VIEWSTATE" value="abc" />'
        '<input type="hidden" name="__VIEWSTATE_SESSION_KEY" value="key-1" />'
        '<input type="hidden" name="__EVENTVALIDATION" value="ev" />'
        '<input type="hidden" name="__VIEWSTATEGENERATOR" value="CA0B0334" />'
        '<input type="hidden" name="__EVENTTARGET" value="" />'
        '<input type="text" name="ignored" value="nope" />'
    )
    fields = parse_hidden_fields(html)

    assert fields == {
        "__VIEWSTATE": "abc",
        "__VIEWSTATE_SESSION_KEY": "key-1",
        "__EVENTVALIDATION": "ev",
    }
