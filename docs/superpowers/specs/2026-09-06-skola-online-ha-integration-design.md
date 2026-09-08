# Škola Online — Home Assistant integration (design)

Date: 2026-09-06
Status: approved, ready for implementation planning

## Goal

Surface a child's school timetable from skolaonline.cz in Home Assistant as a
calendar entity, so the household can see and automate on "what subjects are on
what day".

Scope for this iteration: the timetable only. Grades, absences, messages and
homework are out of scope.

## Constraints

- Škola Online publishes no API. Everything here comes from scraping the parent
  web app at `aplikace.skolaonline.cz`.
- The site is ASP.NET WebForms (Infragistics controls). Server-rendered HTML,
  no XHR, no JSON.
- Their markup can change without notice. The design assumes the parser will
  need occasional repair and degrades gracefully when it does.
- Credentials belong to the user and grant access to their own child's data.

## Findings from the reconnaissance spike

Verified against a live parent account on 2026-09-06. The shapes and behaviour
described below are real; any concrete identifier, name, or value shown as an
example is invented for illustration and does not reproduce anything from that
account.

### Authentication

A plain cross-site form POST. No CSRF token, no viewstate, no captcha, no JS
challenge:

    POST https://aplikace.skolaonline.cz/SOL/Prihlaseni.aspx
      JmenoUzivatele = <username>
      HesloUzivatele = <password>
      btnLogin       = Přihlásit do aplikace

The response is a 302. On failure the `Location` is
`https://www.skolaonline.cz/prihlaseni/?SOLLogin=pass`; on success it points
into `/SOL/App/`. Cookies set: `ASP.NET_SessionId`, `ZPUSOB_OVERENI=SOL`, and
`SERVERID` — the last is a load-balancer sticky cookie, so the client must
retain the whole cookie jar rather than just the session id.

Some schools use "Přihlásit přes Microsoft" SSO instead. That path is not
supported and is out of scope.

### Timetable page

`https://aplikace.skolaonline.cz/SOL/App/Kalendar/KZK001_KalendarTyden.aspx`

A weekly grid. For a parent account this calendar *is* the timetable; the
reference school exposes no separate "Rozvrh" module.

Selecting a week is a WebForms postback. The fields, captured by hooking
`__doPostBack`:

    __EVENTTARGET = ""                      (plain submit, no event target)
    calendarPart_kalendar = <x PostData="2026x9x2026x9x28x1"></x>
    calendarPart$kalendar$RBSelectionMode = "Week"
    CBZobrazitRozvrh = "on"
    CBZobrazitHodnoceni = "on"
    listOfChildrenPart$listOfChildren$DDLChildren = "S001#Z000123"
    __VIEWSTATE, __VIEWSTATE_SESSION_KEY, __EVENTVALIDATION

`PostData` is `YYYYxM x YYYYxMxD x1`: displayed month, then selected date.
Viewstate is held server-side under `__VIEWSTATE_SESSION_KEY`, so the posted
value is a short key and request bodies stay small.

**The selected week is server session state.** A bare GET returns whatever week
was last viewed, not the current one. Every fetch must post an explicit date.

### Grid markup

Table `#CCADynamicCalendarTable`. The header row carries period numbers and
clock times (`0 07:40 - 08:25` … `9 15:25 - 16:10`) in
`.KuvHeaderNadpis` / `.KuvHeaderText`. Each subsequent row is a day, labelled
`Po 14.9.`. Lesson cells are `td.DctCell` containing an inner
`table.DctInnerTableType10`, with `.KuvBunkaRozvrhNadpis` (subject
abbreviation) and `.KuvBunkaRozvrhText` (class and room).

Each cell also carries a tooltip holding the full detail:

    onMouseOverTooltip('ČJ (Český jazyk a literatura) ',
      'Učitel:~Nováková J.~Třída:~9.A~Žáci:~9.A (celá třída)
       ~Učebna:~U101~Cyklus:~bez cyklů~Den (vyuč. hodina):~Po 21.9. (1)')

The tooltip is richer and more reliable than the visible cell text: it gives the
full subject name, the teacher, the room, and an authoritative date and period
number. The parser prefers it and falls back to the visible text.

Non-lesson entries (`Zahájení školního roku`, `2. školní den`) share the grid
but use a different inner-table type class. That is how lessons and events are
told apart.

### Children

The `DDLChildren` select lists a parent's children as `SCHOOL#STUDENT` (e.g.
`S001#Z000123`). The reference account has one child; reading options from the
page makes siblings work without special-casing.

### Session expiry

Observed directly: an idle session redirects to

    https://www.skolaonline.cz/prihlaseni/?utm_source=aplikace.skolaonline.cz
      &utm_medium=Logout&utm_campaign=Logout&Session=Timeout

So the client can detect expiry by host alone — any response whose final URL
leaves `aplikace.skolaonline.cz` means the session is gone. `Session=Timeout`
distinguishes an expiry from a rejected login, but matching on the host is the
more robust test and is what the client uses.

### Not established by the spike

- How substitutions and cancelled lessons render. The reference week contained
  none. A pink `TH` cell suggests cell styling encodes status.

This is handled by the graceful-degradation rules below rather than by
guessing now.

## Decisions

| Question | Decision |
|---|---|
| HA representation | A calendar entity. No sensors in this iteration. |
| Content | Lessons *and* school events, in one calendar. |
| Fetch window | Current week plus the next three. |
| Refresh interval | Every 6 hours. |
| Packaging | HACS-ready repo: `hacs.json`, README, CI running hassfest and HACS validation. |
| Structure | Scraper vendored inside the integration as an HA-agnostic `api/` subpackage. |

The structure decision was taken over two alternatives: a separate
`pyskolaonline` PyPI package (what HA core requires, but two repos and a
publish step before anything is installable — ceremony for an option we may
never exercise), and fully inline logic (fastest to write, but tangles scraping
with HA and drags the HA test harness into parser tests). The vendored package
isolates the code most likely to break behind a plain-Python boundary that can
be tested with saved HTML fixtures, and converts to a separate package later by
moving a folder.

## Architecture

    custom_components/skola_online/
      __init__.py      async_setup_entry: client, coordinator, entry.runtime_data
      config_flow.py   credentials, child selection, reauth
      coordinator.py   SkolaOnlineCoordinator(DataUpdateCoordinator)
      calendar.py      SkolaOnlineCalendar(CoordinatorEntity, CalendarEntity)
      const.py
      manifest.json
      strings.json, translations/{en,cs}.json
      api/
        client.py      login, session handling, week fetch
        parser.py      HTML -> Entry objects
        models.py      Entry, Child
        exceptions.py  InvalidAuth, CannotConnect, ParseError

### api/ — the scraper

Knows nothing about Home Assistant. Takes credentials and an
`aiohttp.ClientSession`, returns dataclasses.

    SkolaOnlineClient(username, password, session)
      async login() -> None
      async list_children() -> list[Child]
      async fetch_week(monday: date, child_id: str | None) -> list[Entry]

`fetch_week` GETs the calendar page, extracts the three hidden fields, then
POSTs back with an explicit `PostData` date, `RBSelectionMode=Week`,
`CBZobrazitRozvrh=on` and the child id. `CBZobrazitHodnoceni` is deliberately
omitted: grades are out of scope, and leaving it off keeps them out of the
parsed grid.

Session expiry surfaces as a redirect to `/prihlaseni/`. The client catches
that, re-logs-in once, and retries the request. This keeps Home Assistant
working across restarts of the school's server without a config entry reload.

`parser.py` is pure: HTML string in, `list[Entry]` out. No network, no clock,
no HA imports. This is what the fixture tests exercise.

### Data model

```python
@dataclass(frozen=True)
class Entry:
    start: datetime              # timezone-aware, HA local time
    end: datetime
    subject: str                 # "ČJ" — abbreviation, used as event summary
    subject_full: str | None     # "Český jazyk a literatura"
    teacher: str | None          # "Nováková J."
    room: str | None             # "U101"
    period: int | None           # 1
    is_lesson: bool              # False for school events

@dataclass(frozen=True)
class Child:
    id: str                      # "S001#Z000123"
    name: str                    # "Dítě Jedno"
```

Start and end times come from the header row's clock times combined with the
tooltip's date, localised with `homeassistant.util.dt`. An entry spanning
several periods collapses into a single `Entry` running from the first
period's start to the last period's end, rather than one per period.

### Coordinator

`DataUpdateCoordinator[dict[date, list[Entry]]]` with a six-hour update
interval. Each cycle fetches four weeks — the Monday of the current week plus
the next three — **sequentially**, because they share one ASP.NET session and
one viewstate. "Current week" means the ISO week containing today in Home
Assistant's local timezone, so on a Sunday the still-current week is fetched
rather than the one starting tomorrow. Results merge into a single date-keyed dict, so re-fetching a
week that has since gained a substitution simply replaces it.

### Calendar entity

`async_get_events(start, end)` filters the coordinator's dict in memory and
performs no I/O, so paging around the calendar card is instant and never
touches the school's server. The required `event` property returns the current
or next upcoming event, which is what makes the entity usable in automations.

Mapping:

- `summary` = `subject` (the abbreviation — short enough for the calendar card)
- `description` = full subject name and teacher
- `location` = `room`

For a school event (`is_lesson` false) there is no abbreviation, so `subject`
holds the event's own title ("2. školní den") and `subject_full`, `teacher` and
`period` are `None`. The mapping is otherwise identical, which keeps the
calendar entity free of branching.

### Config flow

Validates credentials by performing a real login: wrong password raises
`InvalidAuth` on the form, a network failure raises `CannotConnect`. Username
and password are stored in the config entry.

A **reauth flow** prompts for a new password when authentication starts failing,
instead of the integration going quietly stale.

One config entry per child, with `unique_id` set to the child id so the same
child cannot be added twice. With one child this is invisible; with siblings it
yields independently reloadable devices and entities.

## Error handling

Failures are graded rather than uniform:

- Authentication failure during an update raises `ConfigEntryAuthFailed`,
  triggering the reauth flow.
- Network errors and 5xx raise `UpdateFailed`. Home Assistant retains the last
  good data and retries, so a school outage does not blank the dashboard.
- A parse failure on a single cell logs a warning and skips that cell. Their
  template will change eventually; losing one lesson beats losing the week.

## Testing

- **Parser** — plain pytest against HTML fixtures saved from a live account and
  scrubbed of cookies, viewstate and the child id. Covers: a normal week, a
  week containing school events, an empty week, and a malformed cell.
- **Client** — mocked `aiohttp` responses covering successful login, failed
  login, session expiry followed by successful re-login, and the week POST
  carrying the right `PostData`.
- **HA layer** — `pytest-homeassistant-custom-component` covering the config
  flow's success, invalid-auth, cannot-connect, reauth and duplicate-child
  paths, and the calendar entity's `async_get_events` and `event` property.
- **CI** — hassfest and HACS validation on every push.

## Repository scaffolding

`hacs.json`, a README with installation instructions,
`.github/workflows/validate.yml`, and `requirements-test.txt`.
`manifest.json` declares `beautifulsoup4`, which ships with Home Assistant
core, so no new dependency lands in the user's instance.

## Risks

The site is scraped without a contract, using the user's own credentials to
read their own child's data. The markup can change at any time. The graded
error handling and the fixture-based parser tests are the mitigation; expect to
repair the parser once or twice a year.

Microsoft SSO logins are not supported.
