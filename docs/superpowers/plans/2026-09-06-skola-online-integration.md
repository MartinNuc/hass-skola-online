# Škola Online Home Assistant Integration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a HACS-installable Home Assistant custom integration that scrapes a child's timetable from skolaonline.cz and exposes it as a calendar entity.

**Architecture:** A Home-Assistant-agnostic `api/` subpackage does all login and HTML scraping and returns plain dataclasses; a thin HA layer (config flow, `DataUpdateCoordinator`, calendar entity) wraps it. The parser is a pure function — HTML string in, `list[Entry]` out — so it is tested with static fixtures and no HA test harness.

**Tech Stack:** Python 3.13, Home Assistant 2025.3.0+, `aiohttp` (from HA), `beautifulsoup4` (ships with HA core), `pytest`, `pytest-homeassistant-custom-component`.

**Spec:** `docs/superpowers/specs/2026-09-06-skola-online-ha-integration-design.md`

## Global Constraints

- Domain is `skola_online`. Every file lives under `custom_components/skola_online/`.
- Target host is `aplikace.skolaonline.cz`. Login endpoint is `https://aplikace.skolaonline.cz/SOL/Prihlaseni.aspx`. Calendar endpoint is `https://aplikace.skolaonline.cz/SOL/App/Kalendar/KZK001_KalendarTyden.aspx`.
- Nothing in `api/` may import from `homeassistant`. This is the boundary that makes the parser testable; a violation is a review rejection.
- No new runtime dependency beyond `beautifulsoup4`, which HA core already ships.
- Refresh interval is 6 hours. Fetch window is the current ISO week's Monday plus the next three Mondays (4 weeks total).
- Every timetable fetch POSTs an explicit date. Never rely on a bare GET — the server stores the last-viewed week in session state.
- All datetimes handed to Home Assistant are timezone-aware, in HA's configured local timezone.
- Czech day abbreviations in the grid map to weekday offsets: `Po`=0, `Út`=1, `St`=2, `Čt`=3, `Pá`=4, `So`=5, `Ne`=6.
- Never commit real credentials, cookies, `__VIEWSTATE` values, child ids, child names or teacher names. Fixtures are synthetic or scrubbed.

---

### Task 1: Repository scaffolding and integration skeleton

**Files:**
- Create: `custom_components/skola_online/manifest.json`
- Create: `custom_components/skola_online/const.py`
- Create: `custom_components/skola_online/__init__.py`
- Create: `custom_components/skola_online/api/__init__.py`
- Create: `custom_components/skola_online/api/exceptions.py`
- Create: `hacs.json`
- Create: `requirements-test.txt`
- Create: `pytest.ini`
- Create: `.github/workflows/validate.yml`
- Create: `README.md`
- Test: `tests/__init__.py`, `tests/conftest.py`, `tests/test_manifest.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `DOMAIN = "skola_online"`, `BASE_URL`, `LOGIN_URL`, `CALENDAR_URL`, `SCAN_INTERVAL`, `WEEKS_AHEAD`, `CONF_CHILD_ID`, `CONF_CHILD_NAME` from `const.py`; exception classes `SkolaOnlineError`, `CannotConnect`, `InvalidAuth`, `SessionExpired`, `ParseError` from `api/exceptions.py`.

- [ ] **Step 1: Write the failing test**

`tests/test_manifest.py`:

```python
"""The manifest is what HA and HACS validate first — keep it honest."""
import json
from pathlib import Path

MANIFEST = Path("custom_components/skola_online/manifest.json")


def test_manifest_has_required_keys():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["domain"] == "skola_online"
    assert manifest["config_flow"] is True
    assert manifest["iot_class"] == "cloud_polling"
    assert manifest["version"]
    assert manifest["requirements"] == ["beautifulsoup4>=4.12.0"]


def test_api_package_does_not_import_home_assistant():
    """The api/ package must stay HA-agnostic so it can be tested standalone."""
    for path in Path("custom_components/skola_online/api").rglob("*.py"):
        assert "homeassistant" not in path.read_text(encoding="utf-8"), path
```

`tests/__init__.py` and `tests/conftest.py` are empty for now except:

```python
# tests/conftest.py
"""Shared fixtures. Enables custom integrations for the HA test harness."""
import pytest

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """HA refuses to load custom integrations in tests without this."""
    yield
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_manifest.py -v`
Expected: FAIL — `FileNotFoundError` on `manifest.json`.

- [ ] **Step 3: Write minimal implementation**

`custom_components/skola_online/manifest.json`:

```json
{
  "domain": "skola_online",
  "name": "Škola Online",
  "codeowners": ["@MartinNuc"],
  "config_flow": true,
  "documentation": "https://github.com/MartinNuc/hass-skola-online",
  "integration_type": "service",
  "iot_class": "cloud_polling",
  "issue_tracker": "https://github.com/MartinNuc/hass-skola-online/issues",
  "requirements": ["beautifulsoup4>=4.12.0"],
  "version": "0.1.0"
}
```

`custom_components/skola_online/const.py`:

```python
"""Constants for the Škola Online integration."""

from datetime import timedelta
from typing import Final

DOMAIN: Final = "skola_online"

BASE_URL: Final = "https://aplikace.skolaonline.cz"
LOGIN_URL: Final = f"{BASE_URL}/SOL/Prihlaseni.aspx"
CALENDAR_URL: Final = f"{BASE_URL}/SOL/App/Kalendar/KZK001_KalendarTyden.aspx"

# The school's server stores the selected week in session state, so the
# coordinator always posts an explicit date. Six hours is a compromise between
# noticing a substitution and being a polite guest on their server.
SCAN_INTERVAL: Final = timedelta(hours=6)

# Current week plus the next three.
WEEKS_AHEAD: Final = 4

CONF_CHILD_ID: Final = "child_id"
CONF_CHILD_NAME: Final = "child_name"
```

`custom_components/skola_online/api/exceptions.py`:

```python
"""Errors raised by the Škola Online scraper.

These are deliberately independent of Home Assistant's exception types; the
HA layer translates them at its own boundary.
"""


class SkolaOnlineError(Exception):
    """Base class for every error this package raises."""


class CannotConnect(SkolaOnlineError):
    """The school's server could not be reached, or returned a server error."""


class InvalidAuth(SkolaOnlineError):
    """The username or password was rejected."""


class SessionExpired(SkolaOnlineError):
    """The session cookie is no longer valid and a fresh login is required."""


class ParseError(SkolaOnlineError):
    """The page loaded but did not look like the page we expected."""
```

`custom_components/skola_online/api/__init__.py` and
`custom_components/skola_online/__init__.py` are empty placeholders for now
(Task 11 fills the latter in).

`requirements-test.txt`:

```
pytest-homeassistant-custom-component==0.13.316
pytest>=8.0.0
pytest-asyncio>=0.24.0
beautifulsoup4>=4.12.0
aioresponses>=0.7.6
pytest-freezer>=0.4.8
```

`pytest.ini`:

```ini
[pytest]
testpaths = tests
asyncio_mode = auto
```

`hacs.json`:

```json
{
  "name": "Škola Online",
  "content_in_root": false,
  "render_readme": true,
  "homeassistant": "2025.3.0"
}
```

`.github/workflows/validate.yml`:

```yaml
name: Validate

on:
  push:
  pull_request:
  schedule:
    - cron: "0 4 * * 1"

jobs:
  hassfest:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: home-assistant/actions/hassfest@master

  hacs:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: hacs/action@main
        with:
          category: integration

  tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.13"
      - run: pip install -r requirements-test.txt
      - run: python -m pytest -v
```

`README.md` — a stub; Task 12 writes it properly:

```markdown
# Škola Online for Home Assistant

Brings a child's school timetable from [skolaonline.cz](https://www.skolaonline.cz)
into Home Assistant as a calendar entity.

Work in progress. Installation instructions to follow.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pip install -r requirements-test.txt && python -m pytest tests/test_manifest.py -v`
Expected: PASS, 2 tests.

- [ ] **Step 5: Commit**

```bash
git add custom_components hacs.json requirements-test.txt pytest.ini .github README.md tests
git commit -m "feat: scaffold skola_online integration and CI"
```

---

### Task 2: Parser — grid skeleton (periods and day dates)

**Files:**
- Create: `custom_components/skola_online/api/models.py`
- Create: `custom_components/skola_online/api/parser.py`
- Test: `tests/fixtures/__init__.py`, `tests/fixtures/grid.py`, `tests/test_parser_grid.py`

**Interfaces:**
- Consumes: `ParseError` from `api/exceptions.py`.
- Produces:
  - `Period(number: int, start: time, end: time)` — frozen dataclass.
  - `parse_periods(html: str) -> dict[int, Period]` keyed by column index (0-based, counting only the period columns — the day-label column is excluded).
  - `parse_day_rows(html: str, monday: date) -> dict[int, date]` mapping table row index to the calendar date of that day.

Both later parser tasks build on these two functions.

- [ ] **Step 1: Write the failing test**

`tests/fixtures/grid.py` — a synthetic fixture matching the real markup
observed on the live site. Real class names, trimmed to what the parser reads:

```python
"""Synthetic Škola Online grid markup.

The class names, nesting and tooltip format are copied from a live parent
account; the content is invented so no personal data lives in the repo.
"""

HEADER_CELL = (
    '<td><table width="100%" cellspacing="0" cellpadding="0"><tr><td>'
    '<div class="KuvHeaderNadpis">{number}</div>'
    '<div class="KuvHeaderText">{start} - {end}</div>'
    "</td></tr></table></td>"
)

DAY_LABEL_CELL = '<td><div class="KuvDenNadpis">{day}</div><div>{date}</div></td>'


def week_html(rows: str, periods: list[tuple[int, str, str]] | None = None) -> str:
    """Wrap day rows in a full calendar table."""
    periods = periods or [
        (0, "07:40", "08:25"),
        (1, "08:30", "09:15"),
        (2, "09:25", "10:10"),
        (3, "10:30", "11:15"),
    ]
    header = "".join(
        HEADER_CELL.format(number=n, start=s, end=e) for n, s, e in periods
    )
    return (
        '<html><body><table id="CCADynamicCalendarTable" class="DctTable">'
        f"<tr><td></td>{header}</tr>"
        f"{rows}"
        "</table></body></html>"
    )


def day_row(day: str, date_label: str, cells: str) -> str:
    return f"<tr>{DAY_LABEL_CELL.format(day=day, date=date_label)}{cells}</tr>"


EMPTY_CELL = '<td class="DctCellBottom DctCell"></td>'
```

`tests/test_parser_grid.py`:

```python
from datetime import date, time

import pytest

from custom_components.skola_online.api.exceptions import ParseError
from custom_components.skola_online.api.parser import parse_day_rows, parse_periods

from .fixtures.grid import EMPTY_CELL, day_row, week_html


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_parser_grid.py -v`
Expected: FAIL — `ModuleNotFoundError: custom_components.skola_online.api.parser`.

- [ ] **Step 3: Write minimal implementation**

`custom_components/skola_online/api/models.py`:

```python
"""Plain data returned by the scraper. No Home Assistant types here."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time


@dataclass(frozen=True)
class Period:
    """One teaching period, as advertised in the grid's header row."""

    number: int
    start: time
    end: time


@dataclass(frozen=True)
class Child:
    """A child on a parent account, as listed in the DDLChildren dropdown."""

    id: str  # "SCHOOL#STUDENT", e.g. "S001#Z000123"
    name: str


@dataclass(frozen=True)
class Entry:
    """One lesson or school event, resolved to absolute local times."""

    start: datetime
    end: datetime
    subject: str  # abbreviation for a lesson, title for an event
    subject_full: str | None
    teacher: str | None
    room: str | None
    period: int | None
    is_lesson: bool
```

`custom_components/skola_online/api/parser.py`:

```python
"""Turn Škola Online's weekly calendar HTML into Entry objects.

Pure functions: HTML in, dataclasses out. No network, no clock, no Home
Assistant. That is what lets the whole parser be tested against fixtures.
"""

from __future__ import annotations

import re
from datetime import date, time, timedelta

from bs4 import BeautifulSoup, Tag

from .exceptions import ParseError
from .models import Period

GRID_ID = "CCADynamicCalendarTable"

# Czech weekday abbreviations as they appear in the day-label column.
_WEEKDAY_OFFSETS = {"Po": 0, "Út": 1, "St": 2, "Čt": 3, "Pá": 4, "So": 5, "Ne": 6}

_TIME_RANGE = re.compile(r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})")
_DAY_LABEL = re.compile(r"(Po|Út|St|Čt|Pá|So|Ne)")
_DAY_DATE = re.compile(r"(\d{1,2})\.\s*(\d{1,2})\.")


def _grid(html: str) -> Tag:
    soup = BeautifulSoup(html, "html.parser")
    grid = soup.find("table", id=GRID_ID)
    if grid is None:
        raise ParseError(f"no table#{GRID_ID} in the response")
    return grid


def parse_periods(html: str) -> dict[int, Period]:
    """Read the header row. Keys are column indexes, excluding the day column."""
    rows = _grid(html).find_all("tr", recursive=False)
    if not rows:
        raise ParseError("calendar grid has no rows")

    periods: dict[int, Period] = {}
    # Cell 0 is the empty corner above the day labels; periods start at cell 1.
    for column, cell in enumerate(rows[0].find_all("td", recursive=False)[1:]):
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
    rows = _grid(html).find_all("tr", recursive=False)
    days: dict[int, date] = {}

    for index, row in enumerate(rows[1:], start=1):
        cells = row.find_all("td", recursive=False)
        if not cells:
            continue
        label = cells[0].get_text(" ", strip=True)
        weekday = _DAY_LABEL.search(label)
        if weekday is None:
            continue
        days[index] = monday + timedelta(days=_WEEKDAY_OFFSETS[weekday.group(1)])

    return days
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_parser_grid.py -v`
Expected: PASS, 4 tests.

- [ ] **Step 5: Commit**

```bash
git add custom_components/skola_online/api tests
git commit -m "feat: parse calendar grid periods and day dates"
```

---

### Task 3: Parser — lesson cells and tooltips

**Files:**
- Modify: `custom_components/skola_online/api/parser.py`
- Modify: `tests/fixtures/grid.py`
- Test: `tests/test_parser_lessons.py`

**Interfaces:**
- Consumes: `parse_periods`, `parse_day_rows`, `Period`, `Entry`, `ParseError`.
- Produces:
  - `parse_tooltip(attr: str) -> dict[str, str]` — splits an `onMouseOverTooltip(...)` attribute into `{"title": ..., "Učitel": ..., "Učebna": ..., ...}`.
  - `parse_week(html: str, monday: date, tz: tzinfo) -> list[Entry]` — the parser's public entry point, returning entries sorted by start time.

- [ ] **Step 1: Write the failing test**

Append to `tests/fixtures/grid.py`:

```python
TOOLTIP = (
    "onMouseOverTooltip('{abbrev} ({full}) ',"
    "'Učitel:~{teacher}~Třída:~{klass}~Žáci:~{klass} (celá třída)"
    "~Učebna:~{room}~Cyklus:~bez cyklů~Den (vyuč. hodina):~{day} ({period})')"
)

# DctInnerTableType10 is the class the live site uses for a taught lesson.
LESSON_CELL = (
    '<td class="DctCellBottom DctCell"{colspan}>'
    '<table class="DctInnerTableType10" onmouseover="{tooltip}">'
    '<tr><td class="DctInnerTableType10DataTD">'
    '<div class="KuvBunkaRozvrhNadpis">{abbrev}</div>'
    '<div class="KuvBunkaRozvrhText">{klass}{room}</div>'
    "</td></tr></table></td>"
)


def lesson_cell(
    abbrev: str,
    full: str,
    teacher: str,
    room: str,
    day: str,
    period: int,
    klass: str = "9.A",
    colspan: int = 1,
) -> str:
    tooltip = TOOLTIP.format(
        abbrev=abbrev, full=full, teacher=teacher, klass=klass, room=room,
        day=day, period=period,
    ).replace('"', "&quot;")
    return LESSON_CELL.format(
        tooltip=tooltip,
        abbrev=abbrev,
        klass=klass,
        room=room,
        colspan=f' colspan="{colspan}"' if colspan > 1 else "",
    )
```

`tests/test_parser_lessons.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_parser_lessons.py -v`
Expected: FAIL — `ImportError: cannot import name 'parse_tooltip'`.

- [ ] **Step 3: Write minimal implementation**

Add to `custom_components/skola_online/api/parser.py`:

```python
from datetime import datetime, tzinfo

from .models import Entry

LESSON_CLASS = "DctInnerTableType10"

_TOOLTIP_CALL = re.compile(
    r"onMouseOverTooltip\(\s*'(?P<title>.*?)'\s*,\s*'(?P<body>.*?)'\s*\)", re.S
)
_TITLE = re.compile(r"^(?P<abbrev>.*?)\s*\((?P<full>.*)\)\s*$", re.S)


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
    """Parse one week of the calendar grid into Entry objects."""
    periods = parse_periods(html)
    days = parse_day_rows(html, monday)
    rows = _grid(html).find_all("tr", recursive=False)

    entries: list[Entry] = []
    for row_index, day in days.items():
        # Column 0 is the day label; period columns are numbered from there.
        column = 0
        for cell in rows[row_index].find_all("td", recursive=False)[1:]:
            span = int(cell.get("colspan", 1) or 1)
            inner = cell.find("table")
            if inner is not None:
                entry = _build_entry(cell, inner, periods, column, span, day, tz)
                if entry is not None:
                    entries.append(entry)
            column += span

    entries.sort(key=lambda e: (e.start, e.subject))
    return entries


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
    last = periods.get(column + span - 1, first)
    if first is None or last is None:
        return None

    tooltip = _cell_tooltip(cell)
    heading = inner.find(class_="KuvBunkaRozvrhNadpis")
    subject = tooltip.get("abbrev") or (heading.get_text(strip=True) if heading else "")
    if not subject:
        return None

    is_lesson = LESSON_CLASS in (inner.get("class") or [])

    return Entry(
        start=datetime.combine(day, first.start, tzinfo=tz),
        end=datetime.combine(day, last.end, tzinfo=tz),
        subject=subject,
        subject_full=tooltip.get("full"),
        teacher=tooltip.get("Učitel"),
        room=tooltip.get("Učebna"),
        period=_period_number(tooltip.get("Den (vyuč. hodina)", "")) or first.number,
        is_lesson=is_lesson,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_parser_lessons.py tests/test_parser_grid.py -v`
Expected: PASS, 8 tests.

- [ ] **Step 5: Commit**

```bash
git add custom_components/skola_online/api/parser.py tests
git commit -m "feat: parse lesson cells and tooltips into entries"
```

---

### Task 4: Parser — school events, multi-period spans, malformed cells

**Files:**
- Modify: `custom_components/skola_online/api/parser.py`
- Test: `tests/test_parser_events.py`

**Interfaces:**
- Consumes: everything from Task 3.
- Produces: `parse_children(html: str) -> list[Child]` and `parse_hidden_fields(html: str) -> dict[str, str]`, both needed by the client in Tasks 5–7.

- [ ] **Step 1: Write the failing test**

`tests/test_parser_events.py`:

```python
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
        '<option value="S001#Z000123">Dítě Jedno</option>'
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
        '<input type="text" name="ignored" value="nope" />'
    )
    fields = parse_hidden_fields(html)

    assert fields == {
        "__VIEWSTATE": "abc",
        "__VIEWSTATE_SESSION_KEY": "key-1",
        "__EVENTVALIDATION": "ev",
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_parser_events.py -v`
Expected: FAIL — `ImportError: cannot import name 'parse_children'`. The two
`parse_week` tests should already pass once the import resolves, because Task 3
built spans and tolerance in; if they do not, fix `_build_entry`.

- [ ] **Step 3: Write minimal implementation**

Add to `custom_components/skola_online/api/parser.py`:

```python
from .models import Child

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/ -v`
Expected: PASS, 15 tests.

- [ ] **Step 5: Commit**

```bash
git add custom_components/skola_online/api/parser.py tests
git commit -m "feat: parse school events, spans, children and webforms state"
```

---

### Task 5: Client — login and session detection

**Files:**
- Create: `custom_components/skola_online/api/client.py`
- Test: `tests/test_client_login.py`

**Interfaces:**
- Consumes: `LOGIN_URL`, `CALENDAR_URL`, `BASE_URL` from `const.py`; the exceptions from `api/exceptions.py`.
- Produces:
  - `SkolaOnlineClient(username: str, password: str, session: aiohttp.ClientSession, tz: tzinfo)`
  - `async login() -> None`
  - `APP_HOST = "aplikace.skolaonline.cz"`

Later tasks call `login()`, `list_children()` and `fetch_week()` on this class.

- [ ] **Step 1: Write the failing test**

`tests/test_client_login.py`:

```python
import aiohttp
import pytest
from aioresponses import aioresponses

from custom_components.skola_online.api.client import SkolaOnlineClient
from custom_components.skola_online.api.exceptions import CannotConnect, InvalidAuth
from custom_components.skola_online.const import CALENDAR_URL, LOGIN_URL

PRAGUE = "Europe/Prague"


@pytest.fixture
async def client():
    async with aiohttp.ClientSession() as session:
        from zoneinfo import ZoneInfo

        yield SkolaOnlineClient("user", "pass", session, ZoneInfo(PRAGUE))


async def test_login_succeeds_when_redirected_into_the_app(client):
    with aioresponses() as mocked:
        mocked.post(LOGIN_URL, status=302, headers={"Location": CALENDAR_URL})
        mocked.get(CALENDAR_URL, status=200, body="<html>app</html>")

        await client.login()  # must not raise


async def test_login_rejects_a_redirect_back_to_the_public_site(client):
    """A wrong password bounces to www.skolaonline.cz, not into the app."""
    failure = "https://www.skolaonline.cz/prihlaseni/?SOLLogin=pass"
    with aioresponses() as mocked:
        mocked.post(LOGIN_URL, status=302, headers={"Location": failure})
        mocked.get(failure, status=200, body="<html>login</html>")

        with pytest.raises(InvalidAuth):
            await client.login()


async def test_login_raises_cannot_connect_on_a_server_error(client):
    with aioresponses() as mocked:
        mocked.post(LOGIN_URL, status=503)

        with pytest.raises(CannotConnect):
            await client.login()


async def test_login_raises_cannot_connect_on_a_network_failure(client):
    with aioresponses() as mocked:
        mocked.post(LOGIN_URL, exception=aiohttp.ClientConnectionError())

        with pytest.raises(CannotConnect):
            await client.login()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_client_login.py -v`
Expected: FAIL — `ModuleNotFoundError: custom_components.skola_online.api.client`.

- [ ] **Step 3: Write minimal implementation**

`custom_components/skola_online/api/client.py`:

```python
"""HTTP client for the Škola Online parent web app.

Škola Online has no API. This drives the same ASP.NET WebForms pages a browser
would, keeping the full cookie jar (the SERVERID cookie is a load-balancer
sticky cookie, so dropping it breaks the session).
"""

from __future__ import annotations

import logging
from datetime import date, tzinfo
from urllib.parse import quote

import aiohttp

from ..const import CALENDAR_URL, LOGIN_URL
from .exceptions import CannotConnect, InvalidAuth
from .models import Child, Entry

_LOGGER = logging.getLogger(__name__)

APP_HOST = "aplikace.skolaonline.cz"


class SkolaOnlineClient:
    """Logs in and fetches weekly timetables."""

    def __init__(
        self,
        username: str,
        password: str,
        session: aiohttp.ClientSession,
        tz: tzinfo,
    ) -> None:
        self._username = username
        self._password = password
        self._session = session
        self._tz = tz

    async def login(self) -> None:
        """Authenticate and populate the session's cookie jar.

        The app answers a login with a 302: into /SOL/App/ on success, back out
        to www.skolaonline.cz on failure. There is no error body to parse, so
        the landing host is the signal.
        """
        payload = {
            "JmenoUzivatele": self._username,
            "HesloUzivatele": self._password,
            "btnLogin": "Přihlásit do aplikace",
        }
        try:
            async with self._session.post(LOGIN_URL, data=payload) as response:
                if response.status >= 500:
                    raise CannotConnect(f"login returned HTTP {response.status}")
                landed_in_app = response.url.host == APP_HOST
                await response.read()
        except aiohttp.ClientError as err:
            raise CannotConnect(f"could not reach Škola Online: {err}") from err

        if not landed_in_app:
            raise InvalidAuth("username or password rejected")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_client_login.py -v`
Expected: PASS, 4 tests.

- [ ] **Step 5: Commit**

```bash
git add custom_components/skola_online/api/client.py tests
git commit -m "feat: add Škola Online login client"
```

---

### Task 6: Client — fetch a week, with automatic re-login

**Files:**
- Modify: `custom_components/skola_online/api/client.py`
- Test: `tests/test_client_fetch.py`

**Interfaces:**
- Consumes: `parse_week`, `parse_hidden_fields`, `CHILDREN_SELECT` from `parser.py`; `SessionExpired`.
- Produces:
  - `calendar_post_data(day: date) -> str` — module-level helper.
  - `async fetch_week(monday: date, child_id: str | None = None) -> list[Entry]`

- [ ] **Step 1: Write the failing test**

`tests/test_client_fetch.py`:

```python
from datetime import date
from zoneinfo import ZoneInfo

import aiohttp
import pytest
from aioresponses import aioresponses

from custom_components.skola_online.api.client import (
    SkolaOnlineClient,
    calendar_post_data,
)
from custom_components.skola_online.const import CALENDAR_URL, LOGIN_URL

from .fixtures.grid import EMPTY_CELL, day_row, lesson_cell, week_html

PRAGUE = ZoneInfo("Europe/Prague")

HIDDEN = (
    '<input type="hidden" name="__VIEWSTATE" value="vs" />'
    '<input type="hidden" name="__VIEWSTATE_SESSION_KEY" value="sk" />'
    '<input type="hidden" name="__EVENTVALIDATION" value="ev" />'
)


def _week_page() -> str:
    grid = week_html(
        rows=day_row(
            "Po",
            "14.9.",
            EMPTY_CELL
            + lesson_cell("ČJ", "Český jazyk", "Novák J.", "U101", "Po 14.9.", 1)
            + EMPTY_CELL * 2,
        )
    )
    return grid.replace("<body>", f"<body>{HIDDEN}")


@pytest.fixture
async def client():
    async with aiohttp.ClientSession() as session:
        yield SkolaOnlineClient("user", "pass", session, PRAGUE)


def test_calendar_post_data_encodes_the_infragistics_payload():
    assert calendar_post_data(date(2026, 9, 28)) == (
        "%3Cx%20PostData%3D%222026x9x2026x9x28x1%22%3E%3C/x%3E"
    )


async def test_fetch_week_posts_the_requested_date_and_parses_the_result(client):
    with aioresponses() as mocked:
        mocked.get(CALENDAR_URL, status=200, body=_week_page())
        mocked.post(CALENDAR_URL, status=200, body=_week_page())

        entries = await client.fetch_week(date(2026, 9, 14), child_id="S001#D1")

    assert [e.subject for e in entries] == ["ČJ"]

    # aioresponses records the payload dict exactly as it was passed.
    request = next(iter(mocked.requests.values()))[-1]
    sent = request.kwargs["data"]
    assert sent["calendarPart_kalendar"] == calendar_post_data(date(2026, 9, 14))
    assert sent["calendarPart$kalendar$RBSelectionMode"] == "Week"
    assert sent["CBZobrazitRozvrh"] == "on"
    assert sent["__VIEWSTATE"] == "vs"
    assert sent["__VIEWSTATE_SESSION_KEY"] == "sk"
    assert sent["__EVENTVALIDATION"] == "ev"
    assert sent["listOfChildrenPart$listOfChildren$DDLChildren"] == "S001#D1"
    assert "CBZobrazitHodnoceni" not in sent


async def test_fetch_week_relogs_in_once_when_the_session_has_expired(client):
    """An idle session redirects out to www.skolaonline.cz with Session=Timeout."""
    timeout_url = "https://www.skolaonline.cz/prihlaseni/?Session=Timeout"

    with aioresponses() as mocked:
        # First GET lands off-host: the session is gone.
        mocked.get(CALENDAR_URL, status=302, headers={"Location": timeout_url})
        mocked.get(timeout_url, status=200, body="<html>login</html>")
        # Re-login, then the retry succeeds.
        mocked.post(LOGIN_URL, status=302, headers={"Location": CALENDAR_URL})
        mocked.get(CALENDAR_URL, status=200, body=_week_page())
        mocked.get(CALENDAR_URL, status=200, body=_week_page())
        mocked.post(CALENDAR_URL, status=200, body=_week_page())

        entries = await client.fetch_week(date(2026, 9, 14))

    assert [e.subject for e in entries] == ["ČJ"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_client_fetch.py -v`
Expected: FAIL — `ImportError: cannot import name 'calendar_post_data'`.

- [ ] **Step 3: Write minimal implementation**

Add to `custom_components/skola_online/api/client.py`:

```python
from .exceptions import SessionExpired
from .parser import CHILDREN_SELECT, parse_children, parse_hidden_fields, parse_week


def calendar_post_data(day: date) -> str:
    """Build the Infragistics calendar's PostData field value.

    The hidden input holds percent-encoded XML — "<x PostData="Y x M x Y x M x
    D x 1"></x>" — where the first pair is the displayed month and the second
    the selected date. The field value is literally the encoded string; the
    form encoder escapes it a second time on the way out, exactly as a browser
    does.
    """
    stamp = f"{day.year}x{day.month}x{day.year}x{day.month}x{day.day}x1"
    return quote(f'<x PostData="{stamp}"></x>', safe="/")


class SkolaOnlineClient:  # continued
    async def _get_app_page(self, url: str) -> str:
        """GET a page inside the app, raising SessionExpired if bounced out."""
        try:
            async with self._session.get(url) as response:
                if response.status >= 500:
                    raise CannotConnect(f"{url} returned HTTP {response.status}")
                body = await response.text()
                if response.url.host != APP_HOST:
                    raise SessionExpired(f"redirected to {response.url.host}")
        except aiohttp.ClientError as err:
            raise CannotConnect(f"could not reach Škola Online: {err}") from err
        return body

    async def fetch_week(
        self, monday: date, child_id: str | None = None
    ) -> list[Entry]:
        """Fetch one week's timetable.

        Always posts an explicit date: the server remembers the last-viewed
        week in session state, so a bare GET returns whatever was looked at
        last rather than the week we want.
        """
        try:
            return await self._fetch_week(monday, child_id)
        except SessionExpired:
            _LOGGER.debug("session expired, logging in again")
            await self.login()
            return await self._fetch_week(monday, child_id)

    async def _fetch_week(
        self, monday: date, child_id: str | None
    ) -> list[Entry]:
        page = await self._get_app_page(CALENDAR_URL)

        payload: dict[str, str] = {
            "__EVENTTARGET": "",
            "__EVENTARGUMENT": "",
            "__LASTFOCUS": "",
            **parse_hidden_fields(page),
            "calendarPart_kalendar": calendar_post_data(monday),
            "calendarPart$kalendar$RBSelectionMode": "Week",
            # Grades are out of scope; omitting CBZobrazitHodnoceni keeps them
            # out of the returned grid.
            "CBZobrazitRozvrh": "on",
        }
        if child_id:
            payload[CHILDREN_SELECT] = child_id

        try:
            async with self._session.post(CALENDAR_URL, data=payload) as response:
                if response.status >= 500:
                    raise CannotConnect(f"calendar returned HTTP {response.status}")
                body = await response.text()
                if response.url.host != APP_HOST:
                    raise SessionExpired("redirected away during calendar post")
        except aiohttp.ClientError as err:
            raise CannotConnect(f"could not reach Škola Online: {err}") from err

        return parse_week(body, monday=monday, tz=self._tz)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_client_fetch.py -v`
Expected: PASS, 3 tests.

- [ ] **Step 5: Commit**

```bash
git add custom_components/skola_online/api/client.py tests
git commit -m "feat: fetch a timetable week with automatic re-login"
```

---

### Task 7: Client — list children

**Files:**
- Modify: `custom_components/skola_online/api/client.py`
- Test: `tests/test_client_children.py`

**Interfaces:**
- Produces: `async list_children() -> list[Child]`, used by the config flow in Task 9.

- [ ] **Step 1: Write the failing test**

`tests/test_client_children.py`:

```python
from zoneinfo import ZoneInfo

import aiohttp
import pytest
from aioresponses import aioresponses

from custom_components.skola_online.api.client import SkolaOnlineClient
from custom_components.skola_online.const import CALENDAR_URL

PRAGUE = ZoneInfo("Europe/Prague")

PAGE = (
    "<html><body>"
    '<select name="listOfChildrenPart$listOfChildren$DDLChildren">'
    '<option value="S001#D1">Dítě Jedno</option>'
    '<option value="S001#D2">Dítě Druhé</option>'
    "</select></body></html>"
)


@pytest.fixture
async def client():
    async with aiohttp.ClientSession() as session:
        yield SkolaOnlineClient("user", "pass", session, PRAGUE)


async def test_list_children_reads_the_dropdown(client):
    with aioresponses() as mocked:
        mocked.get(CALENDAR_URL, status=200, body=PAGE)

        children = await client.list_children()

    assert [(c.id, c.name) for c in children] == [
        ("S001#D1", "Dítě Jedno"),
        ("S001#D2", "Dítě Druhé"),
    ]


async def test_list_children_is_empty_when_the_account_has_no_dropdown(client):
    with aioresponses() as mocked:
        mocked.get(CALENDAR_URL, status=200, body="<html><body></body></html>")

        assert await client.list_children() == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_client_children.py -v`
Expected: FAIL — `AttributeError: 'SkolaOnlineClient' object has no attribute 'list_children'`.

- [ ] **Step 3: Write minimal implementation**

Add to `custom_components/skola_online/api/client.py`:

```python
    async def list_children(self) -> list[Child]:
        """List the children this account can see.

        A teacher or single-child account may have no dropdown at all, which is
        not an error — it simply means there is nothing to choose.
        """
        page = await self._get_app_page(CALENDAR_URL)
        return parse_children(page)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/ -v`
Expected: PASS, all tests green.

- [ ] **Step 5: Commit**

```bash
git add custom_components/skola_online/api/client.py tests
git commit -m "feat: list children on a parent account"
```

---

### Task 8: Coordinator

**Files:**
- Create: `custom_components/skola_online/coordinator.py`
- Test: `tests/test_coordinator.py`

**Interfaces:**
- Consumes: `SkolaOnlineClient`, `Entry`, the exceptions, `SCAN_INTERVAL`, `WEEKS_AHEAD`, `DOMAIN`.
- Produces:
  - `SkolaOnlineCoordinator(hass, client: SkolaOnlineClient, child_id: str | None)` — a `DataUpdateCoordinator[dict[date, list[Entry]]]`.
  - `mondays_to_fetch(today: date, weeks: int) -> list[date]` — module-level, so it is testable without HA.
  - Attribute `coordinator.data` is `dict[date, list[Entry]]`, keyed by the Monday of each fetched week.

- [ ] **Step 1: Write the failing test**

`tests/test_coordinator.py`:

```python
from datetime import date
from unittest.mock import AsyncMock

import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.skola_online.api.exceptions import CannotConnect, InvalidAuth
from custom_components.skola_online.coordinator import (
    SkolaOnlineCoordinator,
    mondays_to_fetch,
)


def test_mondays_to_fetch_starts_at_the_current_weeks_monday():
    # 2026-09-06 is a Sunday: the current week still starts on 31 August.
    assert mondays_to_fetch(date(2026, 9, 6), weeks=4) == [
        date(2026, 8, 31),
        date(2026, 9, 7),
        date(2026, 9, 14),
        date(2026, 9, 21),
    ]


def test_mondays_to_fetch_on_a_monday_starts_that_day():
    assert mondays_to_fetch(date(2026, 9, 14), weeks=2) == [
        date(2026, 9, 14),
        date(2026, 9, 21),
    ]


async def test_update_keys_entries_by_week(hass):
    client = AsyncMock()
    client.fetch_week.side_effect = lambda monday, child_id=None: [f"lesson-{monday}"]

    coordinator = SkolaOnlineCoordinator(hass, client, child_id="S001#D1")
    data = await coordinator._async_update_data()

    assert len(data) == 4
    assert client.fetch_week.await_count == 4
    for monday, entries in data.items():
        assert entries == [f"lesson-{monday}"]


async def test_invalid_auth_asks_home_assistant_to_reauthenticate(hass):
    client = AsyncMock()
    client.fetch_week.side_effect = InvalidAuth("nope")

    coordinator = SkolaOnlineCoordinator(hass, client, child_id=None)

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()


async def test_the_client_logs_in_once_and_stays_logged_in(hass):
    client = AsyncMock()
    client.fetch_week.return_value = []

    coordinator = SkolaOnlineCoordinator(hass, client, child_id=None)
    await coordinator._async_update_data()
    await coordinator._async_update_data()

    assert client.login.await_count == 1


async def test_a_rejected_password_forces_a_fresh_login_next_time(hass):
    client = AsyncMock()
    client.fetch_week.side_effect = InvalidAuth("nope")

    coordinator = SkolaOnlineCoordinator(hass, client, child_id=None)
    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()

    client.fetch_week.side_effect = None
    client.fetch_week.return_value = []
    await coordinator._async_update_data()

    assert client.login.await_count == 2


async def test_connection_trouble_keeps_the_previous_data(hass):
    client = AsyncMock()
    client.fetch_week.side_effect = CannotConnect("down")

    coordinator = SkolaOnlineCoordinator(hass, client, child_id=None)

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_coordinator.py -v`
Expected: FAIL — `ModuleNotFoundError: custom_components.skola_online.coordinator`.

- [ ] **Step 3: Write minimal implementation**

`custom_components/skola_online/coordinator.py`:

```python
"""Fetches the timetable and hands it to the calendar entity."""

from __future__ import annotations

import logging
from datetime import date, timedelta

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api.client import SkolaOnlineClient
from .api.exceptions import CannotConnect, InvalidAuth, ParseError
from .api.models import Entry
from .const import DOMAIN, SCAN_INTERVAL, WEEKS_AHEAD

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
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
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
            await self.client.login()
            self._authenticated = True

        today = dt_util.now().date()
        weeks: dict[date, list[Entry]] = {}

        for monday in mondays_to_fetch(today, WEEKS_AHEAD):
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
            except (CannotConnect, ParseError) as err:
                raise UpdateFailed(f"could not read week of {monday}: {err}") from err

        return weeks
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_coordinator.py -v`
Expected: PASS, 7 tests.

- [ ] **Step 5: Commit**

```bash
git add custom_components/skola_online/coordinator.py tests
git commit -m "feat: add timetable update coordinator"
```

---

### Task 9: Config flow with child selection and reauth

**Files:**
- Create: `custom_components/skola_online/config_flow.py`
- Create: `custom_components/skola_online/strings.json`
- Create: `custom_components/skola_online/translations/en.json`
- Create: `custom_components/skola_online/translations/cs.json`
- Test: `tests/test_config_flow.py`

**Interfaces:**
- Consumes: `SkolaOnlineClient`, exceptions, `DOMAIN`, `CONF_CHILD_ID`, `CONF_CHILD_NAME`.
- Produces: a config entry whose `data` is `{CONF_USERNAME, CONF_PASSWORD, CONF_CHILD_ID, CONF_CHILD_NAME}` and whose `unique_id` is the child id (or the username when the account exposes no children).

- [ ] **Step 1: Write the failing test**

`tests/test_config_flow.py`:

```python
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.skola_online.api.exceptions import CannotConnect, InvalidAuth
from custom_components.skola_online.api.models import Child
from custom_components.skola_online.const import CONF_CHILD_ID, DOMAIN

CREDENTIALS = {CONF_USERNAME: "parent", CONF_PASSWORD: "secret"}


def _client(children: list[Child]) -> AsyncMock:
    client = AsyncMock()
    client.login.return_value = None
    client.list_children.return_value = children
    return client


async def test_single_child_creates_the_entry_without_a_second_step(hass):
    client = _client([Child(id="S001#D1", name="Dítě Jedno")])

    with patch(
        "custom_components.skola_online.config_flow.SkolaOnlineClient",
        return_value=client,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], CREDENTIALS
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Dítě Jedno"
    assert result["data"][CONF_CHILD_ID] == "S001#D1"


async def test_several_children_prompt_for_a_choice(hass):
    client = _client(
        [Child(id="S001#D1", name="Dítě Jedno"), Child(id="S001#D2", name="Dítě Druhé")]
    )

    with patch(
        "custom_components.skola_online.config_flow.SkolaOnlineClient",
        return_value=client,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], CREDENTIALS
        )
        assert result["step_id"] == "child"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_CHILD_ID: "S001#D2"}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Dítě Druhé"


@pytest.mark.parametrize(
    ("error", "expected"),
    [(InvalidAuth("no"), "invalid_auth"), (CannotConnect("no"), "cannot_connect")],
)
async def test_login_problems_are_reported_on_the_form(hass, error, expected):
    client = AsyncMock()
    client.login.side_effect = error

    with patch(
        "custom_components.skola_online.config_flow.SkolaOnlineClient",
        return_value=client,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], CREDENTIALS
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected}


async def test_the_same_child_cannot_be_added_twice(hass):
    MockConfigEntry(
        domain=DOMAIN, unique_id="S001#D1", data={**CREDENTIALS}
    ).add_to_hass(hass)
    client = _client([Child(id="S001#D1", name="Dítě Jedno")])

    with patch(
        "custom_components.skola_online.config_flow.SkolaOnlineClient",
        return_value=client,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], CREDENTIALS
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_updates_the_stored_password(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="S001#D1",
        data={**CREDENTIALS, CONF_CHILD_ID: "S001#D1"},
    )
    entry.add_to_hass(hass)
    client = _client([Child(id="S001#D1", name="Dítě Jedno")])

    with patch(
        "custom_components.skola_online.config_flow.SkolaOnlineClient",
        return_value=client,
    ):
        result = await entry.start_reauth_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PASSWORD: "brand-new"}
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == "brand-new"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config_flow.py -v`
Expected: FAIL — the flow handler is not registered, so `async_init` raises
`UnknownHandler`.

- [ ] **Step 3: Write minimal implementation**

`custom_components/skola_online/config_flow.py`:

```python
"""Config flow: collect credentials, pick a child, and handle reauth."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

from .api.client import SkolaOnlineClient
from .api.exceptions import CannotConnect, InvalidAuth
from .api.models import Child
from .const import CONF_CHILD_ID, CONF_CHILD_NAME, DOMAIN

_LOGGER = logging.getLogger(__name__)

CREDENTIALS_SCHEMA = vol.Schema(
    {vol.Required(CONF_USERNAME): str, vol.Required(CONF_PASSWORD): str}
)


class SkolaOnlineConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Škola Online."""

    VERSION = 1

    def __init__(self) -> None:
        self._credentials: dict[str, str] = {}
        self._children: list[Child] = []

    async def _authenticate(self, credentials: Mapping[str, str]) -> list[Child]:
        """Log in for real, then read the children the account can see."""
        client = SkolaOnlineClient(
            credentials[CONF_USERNAME],
            credentials[CONF_PASSWORD],
            async_get_clientsession(self.hass),
            dt_util.get_default_time_zone(),
        )
        await client.login()
        return await client.list_children()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                children = await self._authenticate(user_input)
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001 — surface, never crash the flow
                _LOGGER.exception("unexpected error during Škola Online login")
                errors["base"] = "unknown"
            else:
                self._credentials = dict(user_input)
                self._children = children

                if len(children) > 1:
                    return await self.async_step_child()

                child = children[0] if children else None
                return await self._create_entry(child)

        return self.async_show_form(
            step_id="user", data_schema=CREDENTIALS_SCHEMA, errors=errors
        )

    async def async_step_child(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask which child this entry should follow."""
        if user_input is not None:
            chosen = next(
                c for c in self._children if c.id == user_input[CONF_CHILD_ID]
            )
            return await self._create_entry(chosen)

        return self.async_show_form(
            step_id="child",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_CHILD_ID): vol.In(
                        {child.id: child.name for child in self._children}
                    )
                }
            ),
        )

    async def _create_entry(self, child: Child | None) -> ConfigFlowResult:
        """One entry per child, so siblings stay independently reloadable."""
        unique_id = child.id if child else self._credentials[CONF_USERNAME]
        title = child.name if child else self._credentials[CONF_USERNAME]

        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured()

        return self.async_create_entry(
            title=title,
            data={
                **self._credentials,
                CONF_CHILD_ID: child.id if child else None,
                CONF_CHILD_NAME: title,
            },
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        self._credentials = dict(entry_data)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Prompt for a new password rather than going quietly stale."""
        errors: dict[str, str] = {}

        if user_input is not None:
            candidate = {**self._credentials, CONF_PASSWORD: user_input[CONF_PASSWORD]}
            try:
                await self._authenticate(candidate)
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(),
                    data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]},
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            errors=errors,
        )
```

`custom_components/skola_online/strings.json`:

```json
{
  "config": {
    "step": {
      "user": {
        "title": "Škola Online",
        "description": "Sign in with the same details you use at skolaonline.cz.",
        "data": {
          "username": "Username",
          "password": "Password"
        }
      },
      "child": {
        "title": "Choose a child",
        "description": "This account can see more than one child. Add one now; you can add the others afterwards.",
        "data": {
          "child_id": "Child"
        }
      },
      "reauth_confirm": {
        "title": "Sign in again",
        "description": "Škola Online rejected the stored password. Enter the current one.",
        "data": {
          "password": "Password"
        }
      }
    },
    "error": {
      "cannot_connect": "Could not reach Škola Online. Check your connection and try again.",
      "invalid_auth": "That username or password was rejected.",
      "unknown": "Something unexpected went wrong. Check the Home Assistant log."
    },
    "abort": {
      "already_configured": "That child is already set up.",
      "reauth_successful": "Signed in again successfully."
    }
  }
}
```

`translations/en.json` is a byte-for-byte copy of `strings.json`.

`custom_components/skola_online/translations/cs.json`:

```json
{
  "config": {
    "step": {
      "user": {
        "title": "Škola Online",
        "description": "Přihlaste se stejnými údaji jako na skolaonline.cz.",
        "data": {
          "username": "Uživatelské jméno",
          "password": "Heslo"
        }
      },
      "child": {
        "title": "Vyberte dítě",
        "description": "Tento účet vidí více dětí. Přidejte teď jedno, ostatní můžete přidat potom.",
        "data": {
          "child_id": "Dítě"
        }
      },
      "reauth_confirm": {
        "title": "Přihlaste se znovu",
        "description": "Škola Online odmítla uložené heslo. Zadejte prosím aktuální.",
        "data": {
          "password": "Heslo"
        }
      }
    },
    "error": {
      "cannot_connect": "Nepodařilo se spojit se Školou Online. Zkontrolujte připojení a zkuste to znovu.",
      "invalid_auth": "Neplatné uživatelské jméno nebo heslo.",
      "unknown": "Došlo k neočekávané chybě. Podívejte se do logu Home Assistant."
    },
    "abort": {
      "already_configured": "Toto dítě už je nastavené.",
      "reauth_successful": "Přihlášení proběhlo úspěšně."
    }
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_config_flow.py -v`
Expected: PASS, 6 tests.

- [ ] **Step 5: Commit**

```bash
git add custom_components/skola_online tests
git commit -m "feat: add config flow with child selection and reauth"
```

---

### Task 10: Calendar entity

**Files:**
- Create: `custom_components/skola_online/calendar.py`
- Test: `tests/test_calendar.py`

**Interfaces:**
- Consumes: `SkolaOnlineCoordinator`, `Entry`, `SkolaOnlineConfigEntry` (Task 11 defines the alias; until then annotate as `ConfigEntry`).
- Produces: `SkolaOnlineCalendar(coordinator, entry)` and `to_calendar_event(entry: Entry) -> CalendarEvent`.

- [ ] **Step 1: Write the failing test**

`tests/test_calendar.py`:

```python
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


def _calendar(entries: list[Entry]) -> SkolaOnlineCalendar:
    coordinator = MagicMock()
    coordinator.data = {date(2026, 9, 14): entries}
    entry = MagicMock()
    entry.entry_id = "abc"
    entry.title = "Dítě Jedno"
    return SkolaOnlineCalendar(coordinator, entry)


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_calendar.py -v`
Expected: FAIL — `ModuleNotFoundError: custom_components.skola_online.calendar`.

- [ ] **Step 3: Write minimal implementation**

`custom_components/skola_online/calendar.py`:

```python
"""Exposes a child's timetable as a calendar entity."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

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
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
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
```

The `freezer` fixture comes from `pytest-freezer`, already in
`requirements-test.txt` from Task 1.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_calendar.py -v`
Expected: PASS, 5 tests.

- [ ] **Step 5: Commit**

```bash
git add custom_components/skola_online/calendar.py tests
git commit -m "feat: expose the timetable as a calendar entity"
```

---

### Task 11: Wire the integration together

**Files:**
- Modify: `custom_components/skola_online/__init__.py`
- Test: `tests/test_init.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `type SkolaOnlineConfigEntry = ConfigEntry[SkolaOnlineCoordinator]`, `async_setup_entry`, `async_unload_entry`. After this task the integration loads end to end.

Once this task lands, update the annotations in `calendar.py` from `ConfigEntry`
to `SkolaOnlineConfigEntry` so the typed `runtime_data` flows through.

- [ ] **Step 1: Write the failing test**

`tests/test_init.py`:

```python
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.skola_online.const import CONF_CHILD_ID, CONF_CHILD_NAME, DOMAIN


def _entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id="S001#D1",
        title="Dítě Jedno",
        data={
            CONF_USERNAME: "parent",
            CONF_PASSWORD: "secret",
            CONF_CHILD_ID: "S001#D1",
            CONF_CHILD_NAME: "Dítě Jedno",
        },
    )


async def test_setup_creates_the_calendar_entity(hass):
    entry = _entry()
    entry.add_to_hass(hass)

    client = AsyncMock()
    client.fetch_week.return_value = []

    with patch(
        "custom_components.skola_online.SkolaOnlineClient", return_value=client
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get("calendar.dite_jedno") is not None


async def test_unload_removes_the_entity(hass):
    entry = _entry()
    entry.add_to_hass(hass)

    client = AsyncMock()
    client.fetch_week.return_value = []

    with patch(
        "custom_components.skola_online.SkolaOnlineClient", return_value=client
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.NOT_LOADED
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_init.py -v`
Expected: FAIL — setup returns False because `async_setup_entry` does not exist.

- [ ] **Step 3: Write minimal implementation**

`custom_components/skola_online/__init__.py`:

```python
"""The Škola Online integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

from .api.client import SkolaOnlineClient
from .const import CONF_CHILD_ID
from .coordinator import SkolaOnlineCoordinator

PLATFORMS: list[Platform] = [Platform.CALENDAR]

type SkolaOnlineConfigEntry = ConfigEntry[SkolaOnlineCoordinator]


async def async_setup_entry(
    hass: HomeAssistant, entry: SkolaOnlineConfigEntry
) -> bool:
    """Set up Škola Online from a config entry."""
    client = SkolaOnlineClient(
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        async_get_clientsession(hass),
        dt_util.get_default_time_zone(),
    )
    coordinator = SkolaOnlineCoordinator(
        hass, client, child_id=entry.data.get(CONF_CHILD_ID)
    )

    # Raises ConfigEntryAuthFailed or ConfigEntryNotReady for us, so a bad
    # password sends the user to reauth instead of silently loading empty.
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: SkolaOnlineConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/ -v`
Expected: PASS, all tests green.

- [ ] **Step 5: Commit**

```bash
git add custom_components/skola_online tests
git commit -m "feat: wire up config entry setup and unload"
```

---

### Task 12: Fixture capture tooling, README, real-world verification

**Files:**
- Create: `scripts/capture_fixture.py`
- Modify: `README.md`
- Test: `tests/test_real_fixture.py`

**Interfaces:**
- Consumes: `SkolaOnlineClient`.
- Produces: a scrubbed real-world fixture at `tests/fixtures/real_week.html` and a regression test over it.

This task is what proves the synthetic fixtures were not wishful thinking.

- [ ] **Step 1: Write the capture script**

`scripts/capture_fixture.py`:

```python
"""Save one real week of calendar HTML as a scrubbed test fixture.

Usage:
    SKOLA_USER=... SKOLA_PASS=... python scripts/capture_fixture.py 2026-09-14

Credentials come from the environment so they never reach the shell history or
the repository. The saved file has viewstate, cookies, ids and personal names
replaced before it touches disk.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from custom_components.skola_online.api.client import SkolaOnlineClient  # noqa: E402
from custom_components.skola_online.const import CALENDAR_URL  # noqa: E402

OUTPUT = Path("tests/fixtures/real_week.html")

# Names to redact. Extend before running; the script refuses to write a file
# that still contains any of them.
NAMES = [name for name in os.environ.get("SKOLA_REDACT", "").split(",") if name]


def scrub(html: str) -> str:
    """Strip everything that identifies the account or the session."""
    html = re.sub(r'(name="__VIEWSTATE[^"]*"\s+value=")[^"]*"', r"\1REDACTED\"", html)
    html = re.sub(r'(name="__EVENTVALIDATION"\s+value=")[^"]*"', r"\1REDACTED\"", html)
    html = re.sub(r'value="[A-Z]\d+#[A-Z]\d+"', 'value="D000#D000"', html)
    for index, name in enumerate(NAMES, start=1):
        html = html.replace(name, f"Osoba {index}")
    return html


async def main() -> None:
    monday = date.fromisoformat(sys.argv[1])

    async with aiohttp.ClientSession() as session:
        client = SkolaOnlineClient(
            os.environ["SKOLA_USER"],
            os.environ["SKOLA_PASS"],
            session,
            ZoneInfo("Europe/Prague"),
        )
        await client.login()
        # Reach past fetch_week so we keep the raw HTML, not parsed entries.
        await client.fetch_week(monday)
        async with session.get(CALENDAR_URL) as response:
            html = await response.text()

    cleaned = scrub(html)
    for name in NAMES:
        assert name not in cleaned, f"redaction missed {name!r}"

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(cleaned, encoding="utf-8")
    print(f"wrote {OUTPUT} ({len(cleaned)} bytes) — read it before committing")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Capture a real week and read it**

Run, substituting a Monday with a full timetable:

```bash
SKOLA_USER='...' SKOLA_PASS='...' SKOLA_REDACT='Surname F.,Child Name' \
  python scripts/capture_fixture.py 2026-09-14
```

Then **open `tests/fixtures/real_week.html` and read it** before committing.
Confirm by eye that no name, viewstate, cookie or child id survived. If any
did, extend `scrub` and re-run. This is a manual gate; do not skip it.

- [ ] **Step 3: Write the regression test**

`tests/test_real_fixture.py`:

```python
"""Parse a real, scrubbed page. Synthetic fixtures prove the parser is
self-consistent; this one proves it matches the site."""

from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from custom_components.skola_online.api.parser import parse_week

FIXTURE = Path(__file__).parent / "fixtures" / "real_week.html"
PRAGUE = ZoneInfo("Europe/Prague")

pytestmark = pytest.mark.skipif(
    not FIXTURE.exists(), reason="no captured fixture in this checkout"
)


def test_a_real_week_parses_into_lessons():
    entries = parse_week(
        FIXTURE.read_text(encoding="utf-8"), monday=date(2026, 9, 14), tz=PRAGUE
    )

    assert entries, "expected at least one lesson"
    assert all(entry.start < entry.end for entry in entries)
    assert all(entry.subject for entry in entries)
    assert any(entry.is_lesson for entry in entries)
    # Entries come back in chronological order.
    assert entries == sorted(entries, key=lambda e: e.start)


def test_a_real_week_populates_teacher_and_room():
    entries = parse_week(
        FIXTURE.read_text(encoding="utf-8"), monday=date(2026, 9, 14), tz=PRAGUE
    )
    lessons = [entry for entry in entries if entry.is_lesson]

    assert any(lesson.teacher for lesson in lessons)
    assert any(lesson.room for lesson in lessons)
    assert any(lesson.subject_full for lesson in lessons)
```

- [ ] **Step 4: Run the full suite and install into Home Assistant**

Run: `python -m pytest -v`
Expected: PASS, every test including the real-fixture pair.

Then copy `custom_components/skola_online/` into the Home Assistant config
directory, restart, and add the integration through
**Settings → Devices & Services → Add Integration → Škola Online**. Confirm the
calendar entity appears and that a Calendar card shows this week's subjects.

- [ ] **Step 5: Write the README and commit**

Replace the Task 1 stub with:

```markdown
# Škola Online for Home Assistant

Brings a child's school timetable from [skolaonline.cz](https://www.skolaonline.cz)
into Home Assistant as a calendar entity, so you can see the week's subjects on a
dashboard and automate on what is coming next.

Škola Online publishes no API, so this integration signs in and reads the same
parent web pages a browser would. That means it can break when they change their
site; open an issue if it does.

## Installation

**HACS** — in HACS, choose *Integrations → ⋮ → Custom repositories*, add
`https://github.com/MartinNuc/hass-skola-online` as an *Integration*, then
install **Škola Online** and restart Home Assistant.

**Manually** — copy `custom_components/skola_online/` into your Home Assistant
`config/custom_components/` directory and restart.

## Setup

Go to *Settings → Devices & Services → Add Integration* and search for
**Škola Online**. Sign in with your usual username and password. If your account
covers more than one child, pick one; repeat the process to add the others.

Accounts that sign in through *Přihlásit přes Microsoft* are not supported.

## What you get

One calendar entity per child, named after the child. Each lesson is an event:

| Field | Content |
|---|---|
| Summary | Subject abbreviation, e.g. `ČJ` |
| Description | Full subject name and teacher |
| Location | Room |

School events such as `2. školní den` appear on the same calendar, spanning the
periods they occupy.

The timetable refreshes every 6 hours and covers the current week plus the next
three, so substitutions posted during the week are picked up and a Friday still
shows you next Monday.
```

```bash
git add scripts README.md tests
git commit -m "feat: add fixture capture tooling and real-page regression test"
git push
```

---

## Notes for the implementer

- **`api/` never imports `homeassistant`.** Task 1's test enforces this.
- **Always post an explicit date.** The server's session remembers the last
  week viewed; a bare GET returns that instead of what you asked for. This bit
  the reconnaissance spike and will bite you too.
- **Keep the whole cookie jar.** `SERVERID` is a load-balancer sticky cookie;
  dropping it moves you to a different backend and invalidates the session.
- **A single bad cell must not lose the week.** `_build_entry` returns `None`
  rather than raising, on purpose.
- **Never commit real names, ids, cookies or viewstate.** Task 12's manual read
  gate is the last line of defence.
