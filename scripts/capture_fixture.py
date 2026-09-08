"""Save one real week of calendar HTML as a scrubbed test fixture.

Usage:
    .venv/bin/python scripts/capture_fixture.py 2026-09-14          # prompts for the password

The username and the names to redact normally come from a gitignored local
config file, `capture.local.json` at the repo root (override its location
with `--config`):

    {"user": "...", "redact": ["Surname F.", "Child Name"]}

`redact` may also be written as a single comma-separated string, same shape
as `--redact` below. Command-line flags override the config file, which
overrides environment variables, so any of these work:

    .venv/bin/python scripts/capture_fixture.py --user ... --redact 'Surname F.,Child Name' 2026-09-14
    .venv/bin/python scripts/capture_fixture.py --config my-capture.json 2026-09-14
    SKOLA_USER=... SKOLA_REDACT='Surname F.,Child Name' .venv/bin/python scripts/capture_fixture.py 2026-09-14

The password is never a flag and never lives in the config file: it is read
from `SKOLA_PASS` if that's set, otherwise prompted for interactively with
`getpass.getpass()` (nothing echoed, nothing written to shell history). If
`SKOLA_PASS` is unset and stdin isn't a TTY to prompt on, the script fails
fast instead of hanging. CLI arguments land in shell history and are visible
to any other process via `ps`, which is strictly worse than an environment
variable for a secret - so there is no `--password` flag, and a `password`
key in the config file is refused rather than silently ignored.

`--redact` / the config file's `redact` / `SKOLA_REDACT` are all the same
shape: a comma-separated list of names (or a JSON list, for the config file),
each matched as an exact, case-sensitive substring of the page, with
whitespace around commas ignored - so 'Surname F., Child Name' and
'Surname F.,Child Name' redact the same two names. Every name must appear
exactly as the page renders it: the portal shows a teacher as "Surname F."
in some tooltips but by full name elsewhere, so both forms may need listing.
The one exception to "exact": whitespace *within* a name is matched kind-
insensitively (an ordinary space in the config matches a non-breaking space
or similar on the page, and vice versa) - see `_name_pattern()` - because the
portal renders at least one name with a non-breaking space where a human
would type an ordinary one, and the two must be treated as the same name or
redaction silently misses it.

What automated scrubbing covers, and what it doesn't
------------------------------------------------------
Before anything touches disk, the captured page is parsed with BeautifulSoup
and:

- every `__VIEWSTATE*`/`__EVENTVALIDATION` hidden input's value is replaced
  with `REDACTED`;
- every child-id-shaped string (`LETTERS+DIGITS#LETTERS+DIGITS`, e.g.
  `S001#Z000123`) is replaced with a placeholder, wherever it appears — in
  an `<input>`/`<option>` value, in any other tag's attribute (`data-*`,
  `href` query strings, and the like), or in ordinary text content, including
  inside inline `<script>` blocks;
- every configured name to redact is replaced throughout the document.

`verify_scrubbed()` then independently re-scans the *entire* serialised
output — not just the tags scrubbing targeted — and refuses to write the file
if a child-id-shaped value, an un-redacted viewstate/eventvalidation input, or
a configured name is still found, naming what it found.

None of this is a substitute for a human reading the file. Automated
scrubbing can only catch the shapes and names it has been told to look for —
it cannot recognise, say, a teacher's name that isn't in the redaction list, a
free-text comment mentioning a child, or an identifying detail with no fixed
shape at all. **Always open `tests/fixtures/real_week.html` and read it before
committing**, regardless of what this script reports.

A note on fidelity: BeautifulSoup's re-serialisation is not byte-identical to
what the server sent (attributes come out alphabetised, void elements are
self-closed, `&nbsp;` decodes to U+00A0, and so on). That's fine here: the
integration's own parser (`custom_components/skola_online/api/parser.py`)
parses with the identical `BeautifulSoup(html, "html.parser")`, so the parse
tree this script hands back is the same one production would build from the
raw page. The fixture is a normalised capture, not a raw one.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import html as html_entities
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

import aiohttp
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from custom_components.skola_online.api.client import SkolaOnlineClient  # noqa: E402
from custom_components.skola_online.api.parser import (  # noqa: E402
    GRID_ID,
    _rows,
    parse_week,
)

# Anchored to the repository root, not the current working directory, so
# running this from anywhere still writes to (or reads from) the right place.
OUTPUT = ROOT / "tests" / "fixtures" / "real_week.html"
DEFAULT_CONFIG_PATH = ROOT / "capture.local.json"

# Hidden WebForms fields that carry session/page state and must never survive
# a capture. Matched by name, independent of attribute order or quote style.
STATE_FIELD_NAMES = {"__EVENTVALIDATION"}


def _is_state_field(name: str) -> bool:
    return name in STATE_FIELD_NAMES or name.startswith("__VIEWSTATE")


# A child id on the live site has the shape "S001#Z000123" (shape observed on
# a real account; the value above is invented, not reproduced here): one or
# more letters followed by digits, a '#', then the same shape again. The
# school-code shape ("S...") is not guaranteed, so this matches the general
# LETTERS+DIGITS # LETTERS+DIGITS pattern rather than assuming a single
# leading letter. No anchors: it is used both to test a whole attribute value
# and to find and replace occurrences embedded in arbitrary text.
CHILD_ID_RE = re.compile(r"[A-Za-z]+\d+#[A-Za-z]+\d+")
CHILD_ID_PLACEHOLDER = "D000#D000"
REDACTED = "REDACTED"


def parse_redact_names(raw: str) -> list[str]:
    """Split a comma-separated name list, tolerating whitespace around commas.

    Without the strip(), 'A, B' (a perfectly natural way to write the list)
    would yield [' B'] with a leading space: that string never matches the
    page, so the name silently survives scrubbing - and verify_scrubbed()
    would look for the same untrimmed string, so it would not catch the miss
    either. A comma-only entry like ' , ' must still count as empty so the
    "no names configured" hard-fail still triggers.
    """
    return [stripped for name in raw.split(",") if (stripped := name.strip())]


def _normalize_redact(value: Any, *, source: str) -> list[str]:
    """Normalise a config file's `redact` value to a list of names.

    Accepted as either a JSON list of strings or a single comma-separated
    string (the same shape `--redact`/`SKOLA_REDACT` use).
    """
    if isinstance(value, str):
        return parse_redact_names(value)
    if isinstance(value, list):
        names = []
        for item in value:
            if not isinstance(item, str):
                raise SystemExit(
                    f"{source}: 'redact' list entries must be strings, got "
                    f"{item!r}"
                )
            if stripped := item.strip():
                names.append(stripped)
        return names
    raise SystemExit(
        f"{source}: 'redact' must be a JSON list of names or a comma-separated "
        f"string, got {type(value).__name__}"
    )


def _redact_child_ids(text: str) -> str:
    """Replace every child-id-shaped substring of `text` with the placeholder."""
    return CHILD_ID_RE.sub(CHILD_ID_PLACEHOLDER, text)


# Code points that render as inter-word whitespace on the live portal but are
# not the ASCII space (U+0020) a human types when writing --redact/
# SKOLA_REDACT/the config file's `redact` list. U+00A0 (non-breaking space)
# is confirmed live: the portal renders the logged-in parent's name with one
# (BeautifulSoup decodes `&nbsp;` to it), so a two-part name typed with an
# ordinary space never matched it - in either direction - under plain literal
# substring matching. U+202F (narrow no-break space) and U+2009 (thin space)
# are included pre-emptively: nothing on this portal is known to use them,
# but they are visually indistinguishable from an ordinary space and would
# fail identically if they ever showed up. This is an explicit, reviewable
# set rather than the bare `\s` regex class: `\s` also matches things like
# U+3000 (ideographic space) and whatever else Python's Unicode tables
# consider whitespace, which is a much broader and less auditable promise for
# a privacy-critical match than "these specific characters, and no others,
# count as the same space".
_NAME_SPACE_CHARS = "    \t\n\r\f\v"

# Literal HTML entity spellings of a space, matched as plain text rather
# than by decoding entities first. Confirmed live: at least one tooltip on
# the real portal is served double-escaped (`title="Alfa&amp;nbsp;..."`),
# so BeautifulSoup's own (single) entity decoding during parsing turns
# that into the literal six-character text `&nbsp;` sitting in the
# attribute value - not into U+00A0. `_NAME_SPACE_CHARS` above only ever
# sees the latter, so it never matched this. `&#160;` (decimal numeric
# character reference) and `&#xa0;` (hex) are the same character's other
# two common spellings and are included pre-emptively, on the same
# reasoning as U+202F/U+2009 above: nothing on this portal is known to
# double-escape those forms, but they would fail identically if they ever
# did. The hex form's "x" and hex digit vary in case in the wild (`&#xa0;`,
# `&#Xa0;`, `&#xA0;`, ...), so it alone is matched case-insensitively; the
# other two spellings are fixed strings and stay case-sensitive like the
# rest of this pattern.
_NAME_SPACE_ENTITIES = (r"&nbsp;", r"&#160;", r"&#[xX][aA]0;")

# A separator between two name parts is one or more of: a whitespace
# character, or one of the literal entity spellings above - any mixture,
# any number of repeats, since nothing rules out either kind repeating or
# the two kinds combining.
_NAME_SEPARATOR_RE = (
    "(?:[" + _NAME_SPACE_CHARS + "]|" + "|".join(_NAME_SPACE_ENTITIES) + ")+"
)


def _name_pattern(name: str) -> re.Pattern[str]:
    """Compile `name` into a regex that matches it regardless of which kind
    of whitespace - real, or a literal HTML-entity spelling of one -
    separates its parts.

    Splitting `name` on whitespace and re-escaping+rejoining the parts with
    `_NAME_SEPARATOR_RE` means: a name with no internal whitespace round-trips
    through split/join unchanged, so a single-word name still matches
    exactly as a plain literal would; a multi-word name matches an
    occurrence on the page no matter which of `_NAME_SPACE_CHARS` (or a run
    of several, or one of `_NAME_SPACE_ENTITIES`) separates its parts there,
    even if that differs from how the name was typed when configuring
    redaction. This is separator equivalence only - each part is still
    matched verbatim (case-sensitive, no accent-folding), so this cannot
    over-match unrelated page content.
    """
    parts = name.split()
    return re.compile(_NAME_SEPARATOR_RE.join(re.escape(part) for part in parts))


def _compile_name_patterns(
    names: Sequence[str],
) -> list[tuple[re.Pattern[str], str]]:
    """Pair each configured name with its whitespace-tolerant pattern and its
    placeholder, in configured order - shared by `scrub()` and
    `verify_scrubbed()` so both ever look for exactly the same thing."""
    return [
        (_name_pattern(name), f"Osoba {index}")
        for index, name in enumerate(names, start=1)
    ]


def _redact_text(
    text: str, name_patterns: Sequence[tuple[re.Pattern[str], str]]
) -> str:
    """Apply child-id and name redaction to one string of text or one
    attribute value - the single place both kinds of redaction happen, so
    reaching an attribute or a text node for one automatically reaches it
    for the other."""
    redacted = _redact_child_ids(text)
    for pattern, placeholder in name_patterns:
        redacted = pattern.sub(placeholder, redacted)
    return redacted


def scrub(html: str, names: Sequence[str]) -> str:
    """Strip everything that identifies the account or the session.

    Parses with BeautifulSoup rather than regex over raw markup — the same
    approach `parse_hidden_fields` uses to read these fields — so redaction
    does not depend on attribute order (`name` before or after `id`) or quote
    style (single vs. double quotes), both of which a regex anchored on exact
    attribute adjacency would miss.

    Child-id-shaped values are not confined to `<input>`/`<option>` values —
    ASP.NET WebForms pages routinely carry them in `data-*` attributes, `href`
    query strings and inline `<script>` state blobs too — so that redaction is
    applied to every attribute of every tag, and to every text node (which
    also covers script content, since BeautifulSoup exposes it as text).

    `names` is the list of personal names to redact throughout the document.
    Names are redacted through that same attribute/text-node traversal,
    rather than as a separate raw-string pass over the fully serialised
    document afterwards - the previous approach - because a name can appear
    inside an attribute value too (e.g. a `title=` tooltip carrying the
    logged-in parent's name), and because a single traversal is what lets
    `verify_scrubbed()` reuse the exact same matching logic and never
    disagree with what was actually redacted here.
    """
    soup = BeautifulSoup(html, "html.parser")
    name_patterns = _compile_name_patterns(names)

    for element in soup.find_all("input"):
        name = element.get("name")
        if name and _is_state_field(name) and element.get("value") is not None:
            element["value"] = REDACTED

    for tag in soup.find_all(True):
        for attr, value in list(tag.attrs.items()):
            if isinstance(value, str):
                tag.attrs[attr] = _redact_text(value, name_patterns)
            elif isinstance(value, list):
                # BeautifulSoup gives multi-valued attributes (class, rel,
                # headers) as lists. Skipping those left verify_scrubbed() to
                # catch the leak and abort the capture, which is safe but
                # leaves the operator with no way forward.
                tag.attrs[attr] = [_redact_text(item, name_patterns) for item in value]

    for node in soup.find_all(string=True):
        replaced = _redact_text(str(node), name_patterns)
        if replaced != node:
            node.replace_with(replaced)

    return str(soup)


# Characters folded together as "the same separator" by the broad,
# normalising check in `verify_scrubbed()` below - deliberately a much wider
# net than `_NAME_SPACE_CHARS`/`_NAME_SPACE_ENTITIES` above. Those two are
# scoped tightly on purpose, because `scrub()` uses them to *edit* the real
# document and an over-broad match there risks mangling unrelated content.
# `verify_scrubbed()` never edits anything - it only ever decides whether to
# refuse to write - so the asymmetry is safe and deliberate: it folds
# Python's Unicode-aware `\s` (already covering ASCII whitespace, U+00A0 and
# the other Unicode space separators) together with characters that are
# invisible when rendered and so could be used as a de-facto word separator
# without a human ever noticing on screen: zero-width space (U+200B),
# zero-width non-joiner/joiner (U+200C/U+200D), word joiner (U+2060), and
# zero-width no-break space / byte-order mark (U+FEFF). None of this needs
# enumerating exhaustively for a *new* separator to be caught by it - that is
# the entire point of this check versus the enumerate-every-variant approach
# that produced this bug (and the one before it) in the first place.
_VERIFY_FOLD_RE = re.compile(r"[\s\u200b\u200c\u200d\u2060\ufeff]+")


def _normalize_for_verification(text: str) -> str:
    """Best-effort canonical form of `text`, used only by `verify_scrubbed()`
    for its broad, catch-anything name check.

    Two steps:

    1. Decode HTML entities - *twice*. This is deliberate, not a typo: the
       real-world bug this exists to catch is a doubly-escaped entity
       (`&amp;nbsp;` as served), which a *single* `html.unescape()` only
       partially resolves - it turns `&amp;` into `&`, leaving the literal
       text `&nbsp;` behind looking like an entity that was never decoded.
       A second `unescape()` pass decodes that the rest of the way, to
       U+00A0. Unescaping twice is safe for ordinary, singly-escaped input
       too: by the time the first pass has turned `&nbsp;` into U+00A0,
       there is no longer an `&...;` sequence there for the second pass to
       act on, so it is a no-op on already-plain text.
    2. Fold every run of whitespace-ish/invisible characters (see
       `_VERIFY_FOLD_RE`) to a single ASCII space, so that separator
       variants `scrub()` was never taught about still collapse to
       something a plain substring check can find.

    This is intentionally lossy - it exists purely so two occurrences of the
    "same" name, spelled with different separator characters, compare equal.
    It is never used to decide what to *write*, only what to refuse.
    """
    decoded = html_entities.unescape(html_entities.unescape(text))
    return _VERIFY_FOLD_RE.sub(" ", decoded)


def verify_scrubbed(html: str, names: Sequence[str]) -> None:
    """Fail closed unless every known leak vector is actually gone.

    This is the check that has to hold when a human skimming a few hundred
    characters of base64 cannot reliably tell redacted from real, and when a
    child id could be hiding in a location scrubbing did not think to reach.
    It never trusts that scrubbing covered every location: the child-id and
    name checks scan the *entire* serialised document that is about to be
    written, not just the tags `scrub()` targeted.

    The viewstate/eventvalidation check is structural (every `<input>`
    BeautifulSoup parses out of the document) rather than a raw-text scan: a
    content-agnostic regex over arbitrary base64-shaped text would trip on
    unrelated long alphanumeric blobs elsewhere on the page (hashes, encoded
    asset URLs) with no way to tell those apart from a real value. Because
    ASP.NET only ever renders these fields as hidden `<input>` elements, a
    structural pass over every input the page contains covers every place a
    real value could actually appear.

    The name check has two layers, and the second is the important one:

    1. `_name_pattern()` - the exact same pattern-building function `scrub()`
       calls via `_compile_name_patterns()` - rather than a literal substring
       test. Matching literally would let the two silently disagree: a name
       separated on the page by a non-breaking space, or by a literal
       HTML-entity spelling of one, would survive scrubbing while a
       literal-substring verification looked for the same untrimmed,
       separator-sensitive string and found no match either, so it would
       wave the leak through. Reusing the pattern-builder means verification
       always catches precisely what scrubbing was meant to remove.

    2. A second, independent check over a *normalised* view of the document
       (`_normalize_for_verification()`): entities decoded, every run of
       whitespace-ish or invisible characters folded to one ASCII space -
       and the same normalisation applied to each configured name before
       looking for it as a plain substring. This is deliberately broader
       than layer 1 and is not a mirror of it: layer 1 can only ever catch
       separator forms someone has already taught `_name_pattern()` about,
       which is exactly the trap the previous two fixes to this function
       fell into - each closed the one variant found on the real page and
       nothing else. A real name has now survived two such "complete" fixes.
       Layer 2 exists so a *third* unanticipated separator - or a fourth, or
       one nobody ever manually enumerates - still fails closed instead of
       being silently written to disk. Verification is allowed to be, and
       is meant to be, broader than what scrubbing recognises: scrubbing
       edits the real document and so can only act on forms it recognises,
       while verification's only job is to refuse anything it cannot
       positively confirm is gone. "Refuses to write and says why" is the
       correct failure mode here - never "writes with a name still in it".

       One consequence: a name can be caught by layer 2 without `scrub()`
       having removed it, leaving the operator with no way to produce a
       clean file until `scrub()` is taught the new separator. That is the
       accepted trade-off (fail closed), which is why the error raised for
       that case says so explicitly and points at `scrub()`, rather than
       just asserting.

    `names` must be the same list passed to `scrub()`.
    """
    soup = BeautifulSoup(html, "html.parser")

    for element in soup.find_all("input"):
        name = element.get("name")
        if name and _is_state_field(name):
            value = element.get("value")
            if value is not None and value != REDACTED:
                raise AssertionError(
                    f"redaction failed: input name={name!r} still carries a "
                    f"real value"
                )

    for match in CHILD_ID_RE.finditer(html):
        if match.group() != CHILD_ID_PLACEHOLDER:
            raise AssertionError(
                f"redaction failed: a child-id-shaped value survived "
                f"scrubbing ({match.group()!r})"
            )

    for name in names:
        match = _name_pattern(name).search(html)
        if match is not None:
            raise AssertionError(f"redaction failed: name {name!r} survived scrubbing")

    # Layer 2: broad, normalising check - see the docstring above. This is
    # what makes verification fail closed on a separator variant nobody has
    # thought to add to `_name_pattern()` yet, rather than only ever
    # catching the specific list of variants enumerated so far.
    normalized_document = _normalize_for_verification(html)
    for name in names:
        normalized_name = _normalize_for_verification(name)
        if normalized_name and normalized_name in normalized_document:
            raise AssertionError(
                f"redaction failed: name {name!r} was found in the document "
                "after normalising whitespace and HTML entities, in a form "
                "that scrub()'s known-variant matching does not recognise. "
                "The file was NOT written. This is a bug in scrub() worth "
                "reporting/fixing - it needs a case added for whatever "
                "separator or encoding is being used here - since this "
                "check can only tell you the name is still present, not "
                "which raw characters separate its parts."
            )


def describe_grid(html: str) -> str:
    """Summarise the calendar grid's structure - shape only, no content.

    Meant to make a parser failure diagnosable without anyone pasting page
    content around: every line below is an element count or a `class`
    attribute value, never text, never an attribute other than `class`, and
    never an `id` other than the grid table's own (which is a fixed constant,
    not page data).
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", id=GRID_ID)

    lines = [f"table#{GRID_ID} found: {table is not None}"]
    if table is None:
        return "\n".join(lines)

    thead = table.find("thead")
    tbody = table.find("tbody")
    lines.append(f"has thead: {thead is not None}")
    lines.append(f"has tbody: {tbody is not None}")
    lines.append(
        f"rows directly under table: {len(table.find_all('tr', recursive=False))}"
    )
    lines.append(
        f"rows under tbody: "
        f"{len(tbody.find_all('tr', recursive=False)) if tbody else 0}"
    )
    lines.append(
        f"rows under thead: "
        f"{len(thead.find_all('tr', recursive=False)) if thead else 0}"
    )

    # table is known present at this point, so _rows() (which only raises when
    # the grid table itself is missing) cannot fail here.
    rows = _rows(html)

    for row_index, row in enumerate(rows[:3]):
        cells = row.find_all("td", recursive=False)
        lines.append(f"row {row_index}: {len(cells)} direct <td> cells")
        for cell_index, cell in enumerate(cells[:4]):
            descendant_classes = [
                cls for el in cell.find_all(True) for cls in (el.get("class") or [])
            ]
            lines.append(
                f"  cell {cell_index}: class={cell.get('class') or []} "
                f"descendant classes={descendant_classes}"
            )

    all_classes = {
        cls for el in table.find_all(True) for cls in (el.get("class") or [])
    }
    lines.append(
        f"distinct classes in table (up to 40): {sorted(all_classes)[:40]}"
    )

    return "\n".join(lines)


@dataclass(frozen=True)
class CaptureConfig:
    """Everything `main()` needs except the password.

    The password is deliberately not a field here - see `resolve_password()`
    - so no test of `resolve_config()` ever needs to fake one, prompt, or
    touch a TTY.
    """

    user: str
    redact_names: tuple[str, ...]
    monday: date


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="capture_fixture.py",
        description=(
            "Capture one week of the live school portal as a scrubbed test\n"
            "fixture. Username and redaction names can come from a flag, a\n"
            "local config file, or an environment variable, in that order\n"
            "of precedence (first match wins). The password is never a\n"
            "flag and never read from the config file: it comes from\n"
            "SKOLA_PASS if set, otherwise it is prompted for interactively\n"
            "and is never echoed or stored."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "config file (default: capture.local.json at the repo root):\n"
            '  {"user": "...", "redact": ["Surname F.", "Child Name"]}\n'
            "  A 'password' key is refused, not silently ignored - a "
            "plaintext password sitting in the repo directory is exactly "
            "what this script exists to avoid.\n"
            "\n"
            "environment variables (still supported, lowest precedence):\n"
            "  SKOLA_USER, SKOLA_PASS, SKOLA_REDACT (comma-separated, same "
            "shape as --redact)"
        ),
    )
    parser.add_argument(
        "monday",
        help="Monday of the week to capture, as YYYY-MM-DD.",
    )
    parser.add_argument(
        "--user",
        help=(
            "Portal username. Overrides the config file and SKOLA_USER. "
            "The password is never a flag - see SKOLA_PASS in the "
            "environment-variables section below."
        ),
    )
    parser.add_argument(
        "--redact",
        help=(
            "Comma-separated list of personal names to redact from the "
            "captured page, e.g. 'Surname F.,Child Name'. Each name is "
            "matched as an exact, case-sensitive substring of the page "
            "(whitespace within the name matches any kind of whitespace, "
            "so an ordinary space also matches a non-breaking space on the "
            "page); whitespace around commas is ignored, so "
            "'Surname F., Child Name' and 'Surname F.,Child Name' redact "
            "the same two names. Every name must appear exactly as the "
            "page renders it: the portal shows a teacher as 'Surname F.' "
            "in some tooltips but by full name elsewhere, so both forms "
            "may need listing. Overrides the config file and SKOLA_REDACT."
        ),
    )
    parser.add_argument(
        "--config",
        help=(
            "Path to a local JSON config file supplying 'user' and/or "
            f"'redact' (default: {DEFAULT_CONFIG_PATH.name} at the repo "
            "root). A missing file at the default path is not an error as "
            "long as --user/--redact or the environment variables supply "
            "what's needed. Never put a password in this file - see the "
            "config-file section below."
        ),
    )
    return parser


def _load_config_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as err:
        raise SystemExit(f"config file {path} is not valid JSON: {err}") from err

    if not isinstance(data, dict):
        raise SystemExit(f"config file {path} must contain a JSON object")

    if "password" in data:
        raise SystemExit(
            f"config file {path} contains a 'password' key, which is "
            "refused rather than silently ignored: a plaintext password "
            "sitting in the repo directory is exactly what this script "
            "exists to avoid. Remove it and use SKOLA_PASS, or leave both "
            "unset to be prompted interactively."
        )

    return data


def resolve_config(
    argv: Sequence[str],
    environ: Mapping[str, str],
    config_path: Path = DEFAULT_CONFIG_PATH,
) -> CaptureConfig:
    """Resolve everything but the password, from flags, config file and env.

    Precedence, per setting, first match wins: command-line flag, then the
    config file, then the environment variable. `config_path` is the config
    file used when `argv` carries no `--config`; passing `--config` in
    `argv` overrides it. A config file that doesn't exist at the resolved
    path is treated as empty rather than an error, so the other sources can
    still supply everything that's needed.

    Deliberately does not touch the password: see `resolve_password()`.
    """
    args = _build_arg_parser().parse_args(list(argv))

    config_file_path = Path(args.config) if args.config else Path(config_path)
    file_config = _load_config_file(config_file_path)

    user = args.user or file_config.get("user") or environ.get("SKOLA_USER")
    if not user:
        raise SystemExit(
            "no username configured: pass --user, set \"user\" in "
            f"{config_file_path}, or set SKOLA_USER."
        )

    if args.redact is not None:
        redact_names = parse_redact_names(args.redact)
    elif "redact" in file_config:
        redact_names = _normalize_redact(
            file_config["redact"], source=str(config_file_path)
        )
    else:
        redact_names = parse_redact_names(environ.get("SKOLA_REDACT", ""))

    # Required: a capture with no names configured would write a file with
    # no name protection at all, which must never happen silently.
    if not redact_names:
        raise SystemExit(
            "no redaction names configured: pass --redact, set \"redact\" "
            f"in {config_file_path}, or set SKOLA_REDACT. List every "
            "personal name that appears on the page (comma-separated, or "
            "as a JSON list in the config file) - a capture run with "
            "nothing to redact is not a safe default."
        )

    monday = date.fromisoformat(args.monday)

    return CaptureConfig(user=user, redact_names=tuple(redact_names), monday=monday)


def resolve_password(environ: Mapping[str, str]) -> str:
    """Resolve the password, kept separate from `resolve_config()` on purpose.

    `SKOLA_PASS` wins if set - so existing env-var-only usage (and CI) keeps
    working unchanged. Otherwise, prompt interactively with
    `getpass.getpass()`, which echoes nothing and never touches shell
    history. There is deliberately no `--password` flag and no config-file
    field for this: a CLI argument would sit in shell history and be visible
    to any other process via `ps`, and a config file is a plaintext file
    sitting in the repo directory - both strictly worse than an environment
    variable or an interactive prompt for a secret.
    """
    password = environ.get("SKOLA_PASS")
    if password:
        return password

    if not sys.stdin.isatty():
        raise SystemExit(
            "SKOLA_PASS is not set and stdin is not a TTY, so there is no "
            "way to prompt for the password. Set SKOLA_PASS, or run this "
            "interactively so it can be prompted for."
        )

    return getpass.getpass("Škola Online password: ")


async def main() -> None:
    config = resolve_config(sys.argv[1:], os.environ, DEFAULT_CONFIG_PATH)
    password = resolve_password(os.environ)
    tz = ZoneInfo("Europe/Prague")

    async with aiohttp.ClientSession() as session:
        client = SkolaOnlineClient(config.user, password, session, tz)
        await client.login()
        # The raw-HTML fetch, not fetch_week(): capture must not depend on a
        # successful parse. If the parser cannot handle this page, the whole
        # point of this script is to save the page anyway so that failure can
        # be diagnosed.
        html = await client.fetch_week_html(config.monday)

    cleaned = scrub(html, config.redact_names)
    verify_scrubbed(cleaned, config.redact_names)

    # Capture is done as of this write - everything after it is best-effort
    # reporting and must never cause the file to be removed or the script to
    # exit non-zero, however badly the parser chokes on it.
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(cleaned, encoding="utf-8")
    print(f"wrote {OUTPUT} ({len(cleaned)} bytes) — read it before committing")
    print(f"then: git add -f {OUTPUT.relative_to(ROOT)}")

    print()
    print(describe_grid(cleaned))

    print()
    try:
        entries = parse_week(cleaned, monday=config.monday, tz=tz)
    except Exception as err:  # noqa: BLE001 - report, never let capture fail on this
        print(f"parse_week failed ({type(err).__name__}): {err}")
        print(
            "The file above was still saved and is exactly what is needed "
            "to diagnose this failure."
        )
    else:
        print(f"parse_week succeeded: {len(entries)} entries parsed")


if __name__ == "__main__":
    asyncio.run(main())
