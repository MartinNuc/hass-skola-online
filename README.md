# Škola Online for Home Assistant

Brings your child's timetable and homework from
[skolaonline.cz](https://www.skolaonline.cz) into Home Assistant.

- **Calendar** with every lesson and school event
- **Homework sensor** listing current tasks with the teacher's full assignment
- **New homework on a to-do list**, ready to tick off
- **Event** on each new task, for your own automations

Škola Online has no API, so the integration reads the same parent pages a
browser does. If the site changes and something breaks, please
[open an issue](https://github.com/MartinNuc/hass-skola-online/issues).

## Install

Requires Home Assistant 2025.3 or newer.

1. In HACS, go to *⋮ → Custom repositories*. Add
   `https://github.com/MartinNuc/hass-skola-online` as an *Integration*.
2. Install **Škola Online** and restart Home Assistant.
3. Go to *Settings → Devices & Services → Add Integration → Škola Online*.
   Sign in with your usual username and password.

If your account has more than one child, pick one, then add the integration
again for each of the others.

Without HACS, copy `custom_components/skola_online/` into
`config/custom_components/` and restart.

Signing in through *Přihlásit přes Microsoft* is not supported.

## Timetable

Each child gets a calendar named after them (`calendar.<child>`). Every
lesson is an event:

| | Example |
|---|---|
| Title | `Matematika` (or `M`, or `M — Matematika`; see [Options](#options)) |
| Description | Subject and teacher |
| Location | Room |

School events such as `2. školní den` appear on the same calendar.

## Homework

`sensor.<child>_homework` shows how many tasks are on the child's
*Domácí úkoly* page. Its `homework` attribute lists each task with its title,
subject, assigned and due dates, submission status and the teacher's full
assignment text.

### Tracking homework on a to-do list

New homework can be added automatically to a to-do list, where you tick it
off when it's done. Use **Local To-do**: it keeps each task's due date and
the teacher's full text.

1. Go to *Settings → Devices & Services → Add Integration → Local To-do*.
   Name the list, for example `Domácí úkoly`.
2. Open *Škola Online → Configure* and set *Add new homework to* to
   `todo.domaci_ukoly`.

Each task is added once, when it first appears on the site. For example:

| Item | Due | Description |
|---|---|---|
| `Český jazyk a literatura: Psaní číslice 2` | 23. 9. 23:59 | The teacher's assignment text |

When you tick off or delete an item, it doesn't come back. The integration
remembers what it has already added, and that survives restarts. When you
first set this up, every task currently on the site is added.

Other lists work too. A list without due dates or descriptions, such as the
Shopping List, gets the date in the item's name:
`Český jazyk a literatura: Psaní číslice 2 (do 23.9.)`.

### Automations

Every new task fires a `skola_online_new_homework` event. The event data has
`title`, `subject`, `assigned`, `due`, `description`, `id` and
`config_entry_id`. For example, to get a notification on your phone:

```yaml
triggers:
  - trigger: event
    event_type: skola_online_new_homework
actions:
  - action: notify.mobile_app_phone
    data:
      title: "Nový úkol: {{ trigger.event.data.subject }}"
      message: "{{ trigger.event.data.title }}"
```

## Options

These are set per child in *Settings → Devices & Services → Škola Online →
Configure*. Changes apply immediately.

| Setting | Default | |
|---|---|---|
| Refresh interval | 6 hours | 1–24 hours. Used for both the timetable and homework. |
| Weeks to fetch ahead | 4 | 1–8. The current week plus the following ones. |
| Event title | Full subject name | `Matematika`, `M`, or `M — Matematika` |
| Add new homework to | off | Any to-do list |

The refresh interval is capped at hourly so the integration doesn't overload
the school's server.

## Development

```bash
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -r requirements-test.txt
.venv/bin/python -m pytest
```

The tests run on synthetic HTML, plus scrubbed real pages in
`tests/fixtures/`. To capture a fresh timetable page, run:

```bash
.venv/bin/python scripts/capture_fixture.py 2026-09-14
```

The script prompts for your password, which is never stored. Put your
username and the names to redact in a gitignored `capture.local.json`:

```json
{"user": "...", "redact": ["Surname F.", "Child Name", "SCHOOLCODE"]}
```

List your school's code in `redact` as well, because it appears in a
stylesheet URL. The script removes viewstate, child IDs and the names you
list, and it refuses to save the file if any of them remain. It can't catch
personal data it wasn't told about, so **read the file yourself** before you
run `git add -f tests/fixtures/real_week.html`. Run
`scripts/capture_fixture.py --help` for all the options.
