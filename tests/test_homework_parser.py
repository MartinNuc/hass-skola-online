"""Homework list/detail parsing.

The synthetic pages follow the live KUK005 grid (see
fixtures/real_homework_grid.html): header cells numbered by columnno, data
cells by level="row_column", and row-label <th>s that make positional
matching of headers to cells wrong.
"""

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from custom_components.skola_online.api.exceptions import ParseError
from custom_components.skola_online.api.parser import (
    HOMEWORK_CHILDREN_SELECT,
    parse_cz_datetime,
    parse_form_fields,
    parse_homework_description,
    parse_homework_list,
)

PRAGUE = ZoneInfo("Europe/Prague")
TASK_ID = "03e3e121-4ca0-428c-b3ef-fbc4aecaef59"
OTHER_GUID = "11111111-2222-3333-4444-555555555555"

_HEADERS = ["", "", "", "", "", "", "Název úkolu", "Předmět", "Přiděleno", "Termín odevzdání", "Odevzdáno"]


def _row(title, subject, assigned, due, submitted, task_id=TASK_ID, row=0):
    values = ["D626", "D44833", OTHER_GUID, task_id, "D3376709", "",
              title, subject, assigned, due, submitted]
    cells = "".join(
        f'<td level="{row}_{column}"><nobr>{value}</nobr></td>'
        for column, value in enumerate(values)
    )
    return f'<tr id="ctl00xmainxwg_r_{row}" level="{row}"><th class="igtbl_Label"><img/></th>{cells}</tr>'


def _page(*rows: str, children: str = "") -> str:
    headers = '<th><img/></th>' + "".join(
        f'<th columnno="{i}" id="ctl00xmainxwg_c_0_{i}"><nobr>{text}</nobr></th>'
        for i, text in enumerate(_HEADERS)
    )
    return f"""
    <html><body><form>
      <input type="hidden" name="__VIEWSTATE" value="vs"/>
      {children}
      <select name="ctl00$main$ddlStavUkolu"><option value="0" selected>všechny úkoly</option></select>
      <table id="ctl00xmainxwg_main"><tr id="ctl00xmainxwg_mr"><td id="ctl00xmainxwg_mc">
        <div><table><thead><tr>{headers}</tr></thead><tbody>{''.join(rows)}</tbody></table></div>
      </td></tr></table>
    </form></body></html>
    """


def test_the_real_grid_parses():
    html = (Path(__file__).parent / "fixtures" / "real_homework_grid.html").read_text(
        encoding="utf-8"
    )

    [homework] = parse_homework_list(html, PRAGUE)

    assert homework.id == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert homework.title == "Psaní číslice 2"
    assert homework.subject == "Český jazyk a literatura"
    assert homework.assigned == datetime(2026, 9, 22, 9, 19, tzinfo=PRAGUE)
    assert homework.due == datetime(2026, 9, 23, 23, 59, tzinfo=PRAGUE)
    assert homework.submitted == "neodevzdává se"


def test_reads_every_visible_column_and_the_hidden_task_id():
    html = _page(
        _row("Psaní číslice 2", "Český jazyk a literatura", "22.09.2026 09:19",
             "23.9.2026 23:59", "neodevzdává se")
    )

    [homework] = parse_homework_list(html, PRAGUE)

    assert homework.id == TASK_ID
    assert homework.title == "Psaní číslice 2"
    assert homework.subject == "Český jazyk a literatura"
    assert homework.assigned == datetime(2026, 9, 22, 9, 19, tzinfo=PRAGUE)
    assert homework.due == datetime(2026, 9, 23, 23, 59, tzinfo=PRAGUE)
    assert homework.submitted == "neodevzdává se"
    assert homework.description is None


def test_reads_several_rows():
    html = _page(
        _row("A", "Prvouka", "", "24.9.2026", "", task_id=TASK_ID),
        _row("B", "Prvouka", "", "", "", task_id=OTHER_GUID, row=1),
    )

    items = parse_homework_list(html, PRAGUE)

    assert [i.title for i in items] == ["A", "B"]
    assert items[0].due == datetime(2026, 9, 24, 0, 0, tzinfo=PRAGUE)
    assert items[1].due is None
    assert items[1].submitted is None


def test_an_id_that_is_not_a_guid_is_dropped_not_trusted():
    [homework] = parse_homework_list(
        _page(_row("A", "Prvouka", "", "", "", task_id="nonsense")), PRAGUE
    )
    assert homework.id is None


def test_the_homework_page_with_no_grid_is_an_empty_list():
    html = '<select name="ctl00$main$ddlStavUkolu"><option>x</option></select>'
    assert parse_homework_list(html, PRAGUE) == []


def test_a_page_that_is_not_the_homework_page_is_a_parse_error():
    with pytest.raises(ParseError):
        parse_homework_list("<html><body>Přihlášení</body></html>", PRAGUE)


def test_parse_cz_datetime_rejects_nonsense():
    assert parse_cz_datetime("neodevzdává se", PRAGUE) is None
    assert parse_cz_datetime("31.2.2026", PRAGUE) is None


def test_description_keeps_paragraphs_as_lines():
    html = """
    <span id="ctl00_main_uwt__ctl0_lblPodrobneZadaniValue" class="FieldLabel">
      <p>Velká písanka - vzadu</p>
      <p>Děti mají za domácí úkol&nbsp; procvičovat psaní.<br>Dva řádky.</p>
      <p></p>
    </span>
    """
    assert parse_homework_description(html) == (
        "Velká písanka - vzadu\nDěti mají za domácí úkol procvičovat psaní.\nDva řádky."
    )


def test_description_as_plain_text():
    html = '<span id="ctl00_main_uwt__ctl0_lblPodrobneZadaniValue">Str. 12</span>'
    assert parse_homework_description(html) == "Str. 12"


def test_no_description_is_none():
    assert parse_homework_description("<html></html>") is None


def test_form_fields_echo_what_a_browser_would_post():
    html = f"""
    <input type="hidden" name="__VIEWSTATE" value="vs"/>
    <input type="checkbox" name="unticked"/>
    <input type="checkbox" name="ticked" checked/>
    <input type="submit" name="btn" value="Go"/>
    <select name="{HOMEWORK_CHILDREN_SELECT}">
      <option value="S1#A">A</option><option value="S1#B" selected>B</option>
    </select>
    <select name="first"><option value="1">x</option><option value="2">y</option></select>
    """
    assert parse_form_fields(html) == {
        "__VIEWSTATE": "vs",
        "ticked": "on",
        HOMEWORK_CHILDREN_SELECT: "S1#B",
        "first": "1",
    }
