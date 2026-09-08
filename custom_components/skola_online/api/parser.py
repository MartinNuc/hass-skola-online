"""Turn Škola Online's weekly calendar HTML into Entry objects.

Pure functions: HTML in, dataclasses out. No network, no clock, no Home
Assistant. That is what lets the whole parser be tested against fixtures.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, tzinfo

from bs4 import BeautifulSoup, Tag

from .exceptions import ParseError
from .models import Child, Entry, Period

GRID_ID = "CCADynamicCalendarTable"

# DctInnerTableType10 is the class the live site uses for a taught lesson.
LESSON_CLASS = "DctInnerTableType10"

# Some lesson types carry no full name in their tooltip title (see
# tests/fixtures/real_week.html's "TH" cell, whose tooltip title is just
# 'TH ' with no "(...)"). Those types mark their inner data cell with a
# class of their own instead, so this maps that marker class to the full
# name it stands for. The tooltip is still the source of truth for every
# subject it does tell us about - this is only a fallback for the classes
# we have evidence for, not a general abbreviation dictionary, so add to it
# only when a marker class is confirmed against a real page.
MARKER_CLASS_SUBJECT_FULL: dict[str, str] = {
    "KuvOUTridnickaHodina": "Třídnická hodina",
}

# Czech weekday abbreviations as they appear in the day-label column.
_WEEKDAY_OFFSETS = {"Po": 0, "Út": 1, "St": 2, "Čt": 3, "Pá": 4, "So": 5, "Ne": 6}

_TIME_RANGE = re.compile(r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})")
_DAY_LABEL = re.compile(r"(Po|Út|St|Čt|Pá|So|Ne)")

_TOOLTIP_CALL = re.compile(
    r"onMouseOverTooltip\(\s*'(?P<title>.*?)'\s*,\s*'(?P<body>.*?)'\s*\)", re.S
)
_TITLE = re.compile(r"^(?P<abbrev>.*?)\s*\((?P<full>.*)\)\s*$", re.S)


def _grid(html: str) -> Tag:
    soup = BeautifulSoup(html, "html.parser")
    grid = soup.find("table", id=GRID_ID)
    if grid is None:
        raise ParseError(f"no table#{GRID_ID} in the response")
    return grid


def _rows(html: str) -> list[Tag]:
    """The grid's top-level rows: header first, then one per day.

    html.parser does not synthesise a <tbody>, but ASP.NET grid controls do
    emit one often enough that assuming the rows are direct children of the
    table would risk finding none at all on a live page - which would turn
    every fetch into a ParseError.
    """
    grid = _grid(html)
    return (grid.find("tbody") or grid).find_all("tr", recursive=False)


def _cells(row: Tag) -> list[Tag]:
    """A row's own cells. The live grid uses <th> for the header row and for
    each day's label cell, and <td> for lessons; the synthetic fixtures use
    <td> throughout. Treat both alike."""
    return row.find_all(["th", "td"], recursive=False)


def parse_periods(html: str) -> dict[int, Period]:
    """Read the header row. Keys are column indexes, excluding the day column."""
    rows = _rows(html)
    if not rows:
        raise ParseError("calendar grid has no rows")

    periods: dict[int, Period] = {}
    # Cell 0 is the empty corner above the day labels; periods start at cell 1.
    for column, cell in enumerate(_cells(rows[0])[1:]):
        number_el = cell.find(class_="KuvHeaderNadpis")
        text_el = cell.find(class_="KuvHeaderText")
        if number_el is None or text_el is None:
            continue
        match = _TIME_RANGE.search(text_el.get_text())
        if match is None:
            continue
        # "7 pauza" — the period number is the leading integer, if any.
        digits = re.match(r"\s*(\d+)", number_el.get_text())
        if digits is None:
            continue
        start_h, start_m, end_h, end_m = (int(g) for g in match.groups())
        periods[column] = Period(
            number=int(digits.group(1)),
            start=time(start_h, start_m),
            end=time(end_h, end_m),
        )

    if not periods:
        raise ParseError("calendar grid header carried no periods")
    return periods


def parse_day_rows(html: str, monday: date) -> dict[int, date]:
    """Map each day row's index to its date.

    The grid labels days as "Po 14.9." with no year, so the year comes from the
    Monday we asked for. Deriving the date from the weekday offset rather than
    the printed day number keeps a week that straddles New Year correct.
    """
    rows = _rows(html)
    days: dict[int, date] = {}

    for index, row in enumerate(rows[1:], start=1):
        cells = _cells(row)
        if not cells:
            continue
        label = cells[0].get_text(" ", strip=True)
        weekday = _DAY_LABEL.search(label)
        if weekday is None:
            continue
        days[index] = monday + timedelta(days=_WEEKDAY_OFFSETS[weekday.group(1)])

    return days


def parse_tooltip(attr: str) -> dict[str, str]:
    """Split an onMouseOverTooltip attribute into its labelled parts.

    The body is a flat ~-separated list alternating label and value:
    'Učitel:~Novák J.~Učebna:~U101~...'. Returns {} for anything unrecognised
    so callers can fall back to the visible cell text.
    """
    call = _TOOLTIP_CALL.search(attr or "")
    if call is None:
        return {}

    parsed: dict[str, str] = {}
    title = call.group("title").strip()
    if (named := _TITLE.match(title)) is not None:
        parsed["abbrev"] = named.group("abbrev").strip()
        parsed["full"] = named.group("full").strip()
    elif title:
        parsed["abbrev"] = title

    parts = [p.strip() for p in call.group("body").split("~")]
    for label, value in zip(parts[::2], parts[1::2]):
        parsed[label.rstrip(":")] = value

    return parsed


def _cell_tooltip(cell: Tag) -> dict[str, str]:
    for element in [cell, *cell.find_all(True)]:
        attr = element.get("onmouseover", "")
        if "onMouseOverTooltip" in attr:
            return parse_tooltip(attr)
    return {}


def _period_number(text: str) -> int | None:
    """'Po 14.9. (1)' -> 1."""
    match = re.search(r"\((\d+)\)", text or "")
    return int(match.group(1)) if match else None


def parse_week(html: str, monday: date, tz: tzinfo) -> list[Entry]:
    """Parse one week of the calendar grid into Entry objects.

    Entries are sorted by start time, with subject as a tie-break for two
    entries starting at the same time.
    """
    periods = parse_periods(html)
    days = parse_day_rows(html, monday)
    rows = _rows(html)

    entries: list[Entry] = []
    for row_index, day in days.items():
        # Column 0 is the day label; period columns are numbered from there.
        column = 0
        for cell in _cells(rows[row_index])[1:]:
            span = int(cell.get("colspan", 1) or 1)
            inner = cell.find("table")
            if inner is not None:
                entry = _build_entry(cell, inner, periods, column, span, day, tz)
                if entry is not None:
                    entries.append(entry)
            column += span

    entries.sort(key=lambda e: (e.start, e.subject))
    return entries


def _marker_subject_full(inner: Tag) -> str | None:
    """The full name implied by a known marker class on the inner cell.

    Only consulted once the tooltip itself has nothing - see
    MARKER_CLASS_SUBJECT_FULL.
    """
    for marker_class, full_name in MARKER_CLASS_SUBJECT_FULL.items():
        if inner.find(class_=marker_class) is not None:
            return full_name
    return None


def _build_entry(
    cell: Tag,
    inner: Tag,
    periods: dict[int, Period],
    column: int,
    span: int,
    day: date,
    tz: tzinfo,
) -> Entry | None:
    """Build one Entry, or None when the cell is empty or unreadable.

    A single bad cell must never lose the whole week, so anything unexpected
    returns None rather than raising.
    """
    first = periods.get(column)
    if first is None:
        return None
    last = periods.get(column + span - 1, first)

    tooltip = _cell_tooltip(cell)
    heading = inner.find(class_="KuvBunkaRozvrhNadpis")
    subject = tooltip.get("abbrev") or (heading.get_text(strip=True) if heading else "")
    if not subject:
        return None

    is_lesson = LESSON_CLASS in (inner.get("class") or [])

    tooltip_period = _period_number(tooltip.get("Den (vyuč. hodina)", ""))

    return Entry(
        start=datetime.combine(day, first.start, tzinfo=tz),
        end=datetime.combine(day, last.end, tzinfo=tz),
        subject=subject,
        subject_full=tooltip.get("full") or _marker_subject_full(inner),
        teacher=tooltip.get("Učitel"),
        room=tooltip.get("Učebna"),
        period=tooltip_period if tooltip_period is not None else first.number,
        is_lesson=is_lesson,
    )


CHILDREN_SELECT = "listOfChildrenPart$listOfChildren$DDLChildren"


def parse_children(html: str) -> list[Child]:
    """Read the parent's children from the DDLChildren dropdown."""
    soup = BeautifulSoup(html, "html.parser")
    select = soup.find("select", attrs={"name": CHILDREN_SELECT})
    if select is None:
        return []

    children: list[Child] = []
    for option in select.find_all("option"):
        value = (option.get("value") or "").strip()
        name = option.get_text(strip=True)
        if value and name:
            children.append(Child(id=value, name=name))
    return children


def parse_hidden_fields(html: str) -> dict[str, str]:
    """Collect the WebForms state a postback has to echo back.

    Only the three fields the calendar postback needs — harvesting every hidden
    input would drag in per-render junk that changes between requests.
    """
    wanted = {"__VIEWSTATE", "__VIEWSTATE_SESSION_KEY", "__EVENTVALIDATION"}
    soup = BeautifulSoup(html, "html.parser")

    fields: dict[str, str] = {}
    for element in soup.find_all("input", attrs={"type": "hidden"}):
        name = element.get("name")
        if name in wanted:
            fields[name] = element.get("value", "")
    return fields
