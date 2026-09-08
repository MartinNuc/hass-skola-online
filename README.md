# Škola Online for Home Assistant

Brings a child's school timetable from [skolaonline.cz](https://www.skolaonline.cz)
into Home Assistant as a calendar entity, so you can see the week's subjects on a
dashboard and automate on what is coming next.

Škola Online publishes no API, so this integration signs in and reads the same
parent web pages a browser would. That means it can break when they change their
site; open an issue if it does.

## Installation

Requires Home Assistant 2025.3.0 or newer; tested against 2026.2.3.

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
| Summary | Full subject name by default, e.g. `Český jazyk a literatura` — configurable, see Options below |
| Description | Full subject name and teacher |
| Location | Room |

School events such as `2. školní den` appear on the same calendar, spanning the
periods they occupy.

The timetable refreshes every 6 hours by default and covers the current week
plus the next three, so substitutions posted during the week are picked up and
a Friday still shows you next Monday. Both numbers are configurable — see
below.

## Options

From *Settings → Devices & Services → Škola Online → Configure* (per child):

| Setting | Default | Range |
|---|---|---|
| Refresh interval | 6 hours | 1–24 hours |
| Weeks to fetch ahead | 4 | 1–8 |
| Event title | Full subject name | Full subject name / Abbreviation / Abbreviation and full subject name |

The interval is in whole hours, not minutes — this is a school timetable, not
a stock ticker, and the lower bound keeps the integration a polite guest on
skolaonline.cz's server rather than hammering it. Changing any value takes
effect immediately; no restart needed.

Event title controls what a lesson's calendar summary shows: the full
subject name (e.g. `Matematika`), the abbreviation (`M`, what fits in a
calendar card cell), or both together (`M — Matematika`). The description
always keeps the full subject name and teacher, whichever title you pick.
School events such as `2. školní den` are unaffected — they have no separate
abbreviation, so they always show their own title.

## Development: capturing a real-page fixture

`tests/` runs entirely against synthetic HTML except for one regression test
over a real, scrubbed page, which proves the parser matches the actual site
and not just its own synthetic fixtures. To (re)capture it, put your username
and the names to redact in a gitignored `capture.local.json` at the repo root:

```json
{"user": "...", "redact": ["Surname F.", "Child Name"]}
```

and run it with the project's virtualenv interpreter (create one first with
`uv venv --python 3.13 .venv && uv pip install --python .venv/bin/python -r requirements-test.txt`
if you have not already — the script needs `beautifulsoup4` and `aiohttp`):

```bash
.venv/bin/python scripts/capture_fixture.py 2026-09-14   # prompts for the password
```

**The password is never stored and never passed as a flag** — it is read
from `SKOLA_PASS` if that's set in the environment, otherwise the script
prompts for it interactively (`getpass`, so nothing is echoed and nothing
touches shell history). There is deliberately no `--password` flag: a
command-line argument would sit in shell history and be visible to any other
process via `ps`, which is strictly worse than an environment variable or a
prompt for a secret. `capture.local.json` can hold a username and redaction
names but must never hold a password — the script refuses to run if it finds
a `password` key there, rather than silently ignoring it.

`--user`/`--redact` flags override `capture.local.json`, which overrides the
plain environment-variable form (`SKOLA_USER`/`SKOLA_REDACT`, still fully
supported for existing usage and CI):

```bash
.venv/bin/python scripts/capture_fixture.py --user '...' --redact 'Surname F.,Child Name' 2026-09-14
SKOLA_USER='...' SKOLA_REDACT='Surname F.,Child Name' .venv/bin/python scripts/capture_fixture.py 2026-09-14
```

`--redact` (and `capture.local.json`'s `redact`, and `SKOLA_REDACT`) all take
the same shape: a comma-separated list of names (or, in the JSON config, a
JSON list), each matched literally against the page, with whitespace around
commas ignored. Every name must appear exactly as the page renders it — the
portal shows a teacher as "Surname F." in some tooltips but by full name
elsewhere, so both forms may need listing. Run
`.venv/bin/python scripts/capture_fixture.py --help` for the full option reference.

One thing worth adding to `redact` that is easy to overlook: **your school's
code**. The portal serves a per-school stylesheet at a path like
`/SOL/Themes/style.<CODE>.min.css`, so the code appears in a `<link href>` that
has nothing to do with the timetable and does not look like a child id. It
identifies the school, so list it alongside the names.

The script's automated scrubbing covers three things, and only these three
things: viewstate/event-validation input values, child-id-shaped strings
(`LETTERS+DIGITS#LETTERS+DIGITS`) wherever they appear — attributes, plain
text, inline `<script>` blocks — and every configured name to redact. It
refuses to write the file if any of those three checks still finds something
afterwards, scanning the whole captured page rather than trusting that
scrubbing reached everywhere.

That automated pass is **not a substitute for a human reading the file.** It
cannot recognise a form of personal data it wasn't told about — a teacher's
name that didn't make it into `SKOLA_REDACT`, for instance, or an identifying
detail with no fixed shape. `tests/fixtures/real_week.html` is gitignored on
purpose: regardless of what the script reports, **open the file and read it**
before it goes anywhere near a commit, to confirm by eye that no name,
cookie, viewstate or child id survived. Only once you've done that, stage it
deliberately:

```bash
git add -f tests/fixtures/real_week.html
```
