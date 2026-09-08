"""Tests for scripts/capture_fixture.py's structural report and name parsing.

These don't touch the network at all - they exercise the pure helpers that
back Fix 2 (capture-first ordering) and Fix 3 (personal-data-free structural
summary).

Importing scripts.capture_fixture first requires that
custom_components.skola_online has already been imported at least once under
its real path. custom_components is an implicit namespace package; if this
test file is the first to import anything under custom_components, the
namespace can get cached against a stale/partial search path and the
subsequent `from custom_components.skola_online... import ...` inside
capture_fixture.py raises ModuleNotFoundError. Importing a submodule directly
here first primes that cache correctly, independent of test run order.
"""

import json
from datetime import date

import pytest
from bs4 import BeautifulSoup

import custom_components.skola_online.api.parser  # noqa: F401

import scripts.capture_fixture as capture_fixture

from .fixtures.grid import EMPTY_CELL, day_row, lesson_cell, week_html


def test_parse_redact_names_ignores_whitespace_around_commas():
    spaced = capture_fixture.parse_redact_names(" Nováková J. , Dítě Jedno ")
    tight = capture_fixture.parse_redact_names("Nováková J.,Dítě Jedno")

    assert spaced == tight == ["Nováková J.", "Dítě Jedno"]


def test_parse_redact_names_treats_comma_only_input_as_empty():
    assert capture_fixture.parse_redact_names(" , ") == []
    assert capture_fixture.parse_redact_names("") == []


def _sample_grid_html() -> str:
    return week_html(
        rows=day_row(
            "Po",
            "14.9.",
            EMPTY_CELL
            + lesson_cell("ČJ", "Český jazyk", "Novák J.", "U101", "Po 14.9.", 1)
            + EMPTY_CELL * 2,
        )
    )


def test_describe_grid_reports_structure_for_a_synthetic_grid():
    report = capture_fixture.describe_grid(_sample_grid_html())

    assert "table#CCADynamicCalendarTable found: True" in report
    assert "has thead: False" in report
    assert "has tbody: False" in report
    # header row + one day row, none wrapped in <thead>/<tbody>
    assert "rows directly under table: 2" in report
    assert "rows under tbody: 0" in report
    assert "rows under thead: 0" in report

    # Row 0 is the header row: 5 cells (empty corner + 4 periods).
    assert "row 0: 5 direct <td> cells" in report
    assert "KuvHeaderNadpis" in report
    assert "KuvHeaderText" in report

    # Row 1 is the day row: day label + 4 grid cells.
    assert "row 1: 5 direct <td> cells" in report
    assert "KuvDenNadpis" in report
    assert "DctInnerTableType10" in report

    assert "distinct classes in table (up to 40):" in report
    # No cell text, teacher names or room numbers leaked into the report.
    assert "Novák" not in report
    assert "U101" not in report
    assert "Český jazyk" not in report


def test_describe_grid_does_not_crash_when_the_table_is_absent():
    report = capture_fixture.describe_grid("<html><body>no grid here</body></html>")

    assert report == "table#CCADynamicCalendarTable found: False"


def test_describe_grid_does_not_crash_on_empty_input():
    report = capture_fixture.describe_grid("")

    assert report == "table#CCADynamicCalendarTable found: False"


# --- resolve_config() precedence ------------------------------------------
#
# None of these tests supply a password, prompt, or touch a real TTY:
# resolve_config() deliberately never resolves the password (that's
# resolve_password(), tested separately below without ever calling
# getpass.getpass).


def _write_config(tmp_path, data: dict) -> "Path":
    path = tmp_path / "capture.local.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_resolve_config_flag_beats_config_file_beats_environment(tmp_path):
    config_path = _write_config(
        tmp_path, {"user": "config-user", "redact": "Config Name"}
    )
    environ = {"SKOLA_USER": "env-user", "SKOLA_REDACT": "Env Name"}

    # No flags: config file wins over environment.
    config = capture_fixture.resolve_config(["2026-09-14"], environ, config_path)
    assert config.user == "config-user"
    assert config.redact_names == ("Config Name",)

    # Flags win over both the config file and the environment.
    config = capture_fixture.resolve_config(
        ["--user", "flag-user", "--redact", "Flag Name", "2026-09-14"],
        environ,
        config_path,
    )
    assert config.user == "flag-user"
    assert config.redact_names == ("Flag Name",)
    assert config.monday == date(2026, 9, 14)


def test_resolve_config_from_config_file_alone(tmp_path):
    config_path = _write_config(
        tmp_path, {"user": "config-user", "redact": ["Name One", "Name Two"]}
    )

    config = capture_fixture.resolve_config(["2026-09-14"], {}, config_path)

    assert config.user == "config-user"
    assert config.redact_names == ("Name One", "Name Two")


def test_resolve_config_from_environment_alone(tmp_path):
    missing_config = tmp_path / "capture.local.json"
    environ = {"SKOLA_USER": "env-user", "SKOLA_REDACT": "Env Name, Other Name"}

    config = capture_fixture.resolve_config(
        ["2026-09-14"], environ, missing_config
    )

    assert config.user == "env-user"
    assert config.redact_names == ("Env Name", "Other Name")


def test_resolve_config_redact_list_and_comma_string_are_equivalent(tmp_path):
    # Two separate config files: one using a JSON list, one using a
    # comma-separated string, both for the same two names.
    list_config = tmp_path / "list.json"
    list_config.write_text(
        json.dumps({"user": "u", "redact": ["Name One", "Name Two"]}),
        encoding="utf-8",
    )
    string_config = tmp_path / "string.json"
    string_config.write_text(
        json.dumps({"user": "u", "redact": "Name One,Name Two"}), encoding="utf-8"
    )

    from_list = capture_fixture.resolve_config(["2026-09-14"], {}, list_config)
    from_string = capture_fixture.resolve_config(["2026-09-14"], {}, string_config)

    assert from_list.redact_names == from_string.redact_names == (
        "Name One",
        "Name Two",
    )


def test_resolve_config_redact_ignores_whitespace_around_commas(tmp_path):
    environ = {"SKOLA_USER": "u", "SKOLA_REDACT": " Name One , Name Two "}

    config = capture_fixture.resolve_config(
        ["2026-09-14"], environ, tmp_path / "missing.json"
    )

    assert config.redact_names == ("Name One", "Name Two")


def test_resolve_config_hard_fails_on_empty_redaction_list_via_flag(tmp_path):
    environ = {"SKOLA_USER": "u"}

    with pytest.raises(SystemExit):
        capture_fixture.resolve_config(
            ["--redact", " , ", "2026-09-14"], environ, tmp_path / "missing.json"
        )


def test_resolve_config_hard_fails_on_empty_redaction_list_via_environment(tmp_path):
    environ = {"SKOLA_USER": "u", "SKOLA_REDACT": "   "}

    with pytest.raises(SystemExit):
        capture_fixture.resolve_config(
            ["2026-09-14"], environ, tmp_path / "missing.json"
        )


def test_resolve_config_refuses_a_config_file_containing_a_password(tmp_path):
    config_path = _write_config(
        tmp_path,
        {"user": "u", "redact": "Name", "password": "hunter2"},
    )

    with pytest.raises(SystemExit, match="password"):
        capture_fixture.resolve_config(["2026-09-14"], {}, config_path)


def test_resolve_config_missing_config_file_is_not_an_error(tmp_path):
    missing_config = tmp_path / "does-not-exist.json"
    environ = {"SKOLA_USER": "env-user", "SKOLA_REDACT": "Env Name"}

    config = capture_fixture.resolve_config(
        ["2026-09-14"], environ, missing_config
    )

    assert config.user == "env-user"
    assert config.redact_names == ("Env Name",)


def test_resolve_config_fails_clearly_when_nothing_supplies_a_user(tmp_path):
    with pytest.raises(SystemExit):
        capture_fixture.resolve_config(
            ["2026-09-14"], {}, tmp_path / "missing.json"
        )


def test_resolve_config_config_flag_overrides_default_path(tmp_path):
    default_path = tmp_path / "capture.local.json"
    # Deliberately never written - if resolve_config looked at the default
    # instead of --config, this would hard-fail on a missing user.
    explicit_path = tmp_path / "explicit.json"
    explicit_path.write_text(
        json.dumps({"user": "explicit-user", "redact": "Name"}), encoding="utf-8"
    )

    config = capture_fixture.resolve_config(
        ["--config", str(explicit_path), "2026-09-14"], {}, default_path
    )

    assert config.user == "explicit-user"


# --- resolve_password() ----------------------------------------------------
#
# Only the SKOLA_PASS-is-set path is testable without faking a TTY or a
# real interactive prompt - which the task explicitly rules out testing.


def test_resolve_password_uses_skola_pass_when_set():
    assert capture_fixture.resolve_password({"SKOLA_PASS": "secret"}) == "secret"


# --- scrub() / verify_scrubbed(): whitespace-tolerant name matching --------
#
# This is the fix for a real, just-discovered bug: the live portal renders
# the logged-in parent's name with a non-breaking space (U+00A0) between the
# parts, which BeautifulSoup decodes `&nbsp;` to. A name configured with an
# ordinary space (the natural way to type one) never matched that literally,
# so scrubbing silently skipped it *and* verify_scrubbed() - which checked
# for the exact same untrimmed literal - didn't notice either. A capture was
# written and reported clean with a real name still in it. These tests prove
# scrub() and verify_scrubbed() now treat an ordinary space and an NBSP (or
# similar) between name parts as the same separator, in both directions, in
# both text and attribute values, while leaving single-word names and
# unrelated redaction (child ids, viewstate) unaffected.

NBSP = " "  # what BeautifulSoup decodes `&nbsp;` to


def test_scrub_matches_ordinary_space_configured_name_against_nbsp_on_page():
    # The page renders the name with an NBSP; the name is configured with an
    # ordinary space, as a human naturally would.
    html = f"<p>Jmeno: Alfa{NBSP}Betova</p>"

    cleaned = capture_fixture.scrub(html, ["Alfa Betova"])

    assert "Osoba 1" in cleaned
    assert "Alfa" not in cleaned
    assert "Betova" not in cleaned


def test_scrub_matches_nbsp_configured_name_against_ordinary_space_on_page():
    # The reverse direction: the name is configured with an NBSP (e.g. typed
    # by copy-pasting straight off the page), but this particular occurrence
    # on the page happens to use an ordinary space.
    nbsp_configured_name = f"Alfa{NBSP}Betova"
    html = "<p>Jmeno: Alfa Betova</p>"

    cleaned = capture_fixture.scrub(html, [nbsp_configured_name])

    assert "Osoba 1" in cleaned
    assert "Alfa" not in cleaned
    assert "Betova" not in cleaned


def test_scrub_matches_whitespace_variant_name_inside_attribute_value_too():
    # The exact shape of the real bug: the name appears once in a `title`
    # attribute (NBSP-separated) and once in text (ordinary space).
    html = (
        f'<span title="Alfa{NBSP}Betova" class="username">Alfa Betova</span>'
    )

    cleaned = capture_fixture.scrub(html, ["Alfa Betova"])

    assert "Alfa" not in cleaned
    assert "Betova" not in cleaned
    assert cleaned.count("Osoba 1") == 2


def test_verify_scrubbed_refuses_when_nbsp_separated_name_would_survive():
    # Constructs the precise pre-fix failure: a document that literal
    # substring scrubbing (matching an ordinary-space name) would have left
    # untouched, because this occurrence is NBSP-separated. Before the fix,
    # verify_scrubbed() looked for the same untrimmed literal and would not
    # have noticed either, so the file would have been written and reported
    # clean. It must now refuse.
    leftover = f'<span class="username">Alfa{NBSP}Betova</span>'

    with pytest.raises(AssertionError, match="Alfa Betova"):
        capture_fixture.verify_scrubbed(leftover, ["Alfa Betova"])


def test_scrub_single_word_name_still_behaves_exactly_like_a_literal():
    html = "<p>Novak was here</p>"

    cleaned = capture_fixture.scrub(html, ["Novak"])

    assert cleaned == "<p>Osoba 1 was here</p>"


def test_verify_scrubbed_passes_a_single_word_name_that_was_actually_removed():
    cleaned = capture_fixture.scrub("<p>Novak was here</p>", ["Novak"])

    capture_fixture.verify_scrubbed(cleaned, ["Novak"])  # must not raise


def test_verify_scrubbed_still_catches_a_plain_unredacted_single_word_name():
    with pytest.raises(AssertionError, match="Novak"):
        capture_fixture.verify_scrubbed("<p>Novak was here</p>", ["Novak"])


def test_scrub_end_to_end_regression_for_the_original_literal_matching_bug():
    # The regression test for the original bug report: scrubbing a document
    # containing an ordinary-space, two-word name configured the same way
    # must remove both words - this must hold even with no whitespace
    # mismatch at all, which is the simplest case the original code was
    # supposed to (and did) handle, guarded here so it can never silently
    # break again.
    html = "<html><body><p>Some Name is here</p></body></html>"

    cleaned = capture_fixture.scrub(html, ["Some Name"])

    assert "Some" not in cleaned
    assert "Name" not in cleaned


# --- scrub() / verify_scrubbed(): child-id and viewstate, unaffected -------


def test_scrub_still_redacts_child_id_shaped_values_in_text_and_attribute():
    html = '<div data-child="S001#Z000123">S001#Z000123 details</div>'

    cleaned = capture_fixture.scrub(html, [])

    assert "S001#Z000123" not in cleaned
    assert cleaned.count(capture_fixture.CHILD_ID_PLACEHOLDER) == 2


def test_scrub_still_redacts_viewstate_and_eventvalidation_inputs():
    html = (
        '<input type="hidden" name="__VIEWSTATE" value="abc123==" />'
        '<input type="hidden" name="__EVENTVALIDATION" value="xyz==" />'
    )

    cleaned = capture_fixture.scrub(html, [])

    assert "abc123==" not in cleaned
    assert "xyz==" not in cleaned
    assert cleaned.count(capture_fixture.REDACTED) == 2


def test_verify_scrubbed_still_raises_on_a_leftover_viewstate_value():
    html = '<input type="hidden" name="__VIEWSTATE" value="abc123==" />'

    with pytest.raises(AssertionError, match="__VIEWSTATE"):
        capture_fixture.verify_scrubbed(html, [])


def test_verify_scrubbed_still_raises_on_a_leftover_child_id():
    html = "<p>S001#Z000123</p>"

    with pytest.raises(AssertionError, match="child-id"):
        capture_fixture.verify_scrubbed(html, [])


def test_verify_scrubbed_passes_a_fully_clean_document():
    html = capture_fixture.scrub(
        '<input type="hidden" name="__VIEWSTATE" value="abc==" />'
        '<div data-child="S001#Z000123">Alfa Betova</div>',
        ["Alfa Betova"],
    )

    capture_fixture.verify_scrubbed(html, ["Alfa Betova"])  # must not raise


# --- scrub() / verify_scrubbed(): entity-escaped separators ---------------
#
# The third bug in this scrubber, found against the real captured page: a
# `title=` tooltip is served *double*-escaped by the portal
# (`title="Alfa&amp;nbsp;Betova"`). BeautifulSoup's own entity decoding
# during parsing only unwraps one layer of that, turning `&amp;` into `&`
# and leaving the attribute value as the literal six-character text
# `&nbsp;` sitting between the name's two parts - not U+00A0. The previous
# fix's separator class only ever looked for real whitespace characters
# (including U+00A0), so it never matched this literal text, and
# `_name_pattern()`-based verification shared that exact blind spot and
# didn't notice either: a real name survived scrubbing a second time.
#
# Part 1 below (scrub() extended to recognise the literal entity spellings
# `&nbsp;`, `&#160;`, `&#xa0;` as separators too) closes this *specific*
# variant. Part 2 is the one that matters going forward: verify_scrubbed()
# now also checks a normalised (entities decoded, whitespace/invisible
# characters folded) view of the document, so a *fourth* separator variant
# nobody has thought of yet still fails closed instead of being silently
# written. The zero-width-space test below proves that: it is a variant
# scrub() deliberately does NOT recognise, and verify_scrubbed() must still
# catch it.

# What the raw served markup double-escapes `&nbsp;` to, once BeautifulSoup
# decodes one layer of entities during parsing - the literal six characters
# '&', 'n', 'b', 's', 'p', ';', not U+00A0. Built by parsing rather than
# typed as a literal, so the test can't typo its way past the real bug.
_LITERAL_NBSP_ENTITY = BeautifulSoup(
    "&amp;nbsp;", "html.parser"
).get_text()
assert _LITERAL_NBSP_ENTITY == "&nbsp;"


def test_scrub_redacts_name_separated_by_literal_nbsp_entity_in_attribute():
    # Raw markup as actually served: double-escaped, so BeautifulSoup's
    # parse-time decoding leaves the literal text '&nbsp;' in the attribute.
    html = '<span title="Alfa&amp;nbsp;Betova">x</span>'

    cleaned = capture_fixture.scrub(html, ["Alfa Betova"])

    assert "Alfa" not in cleaned
    assert "Betova" not in cleaned
    assert "Osoba 1" in cleaned


def test_verify_scrubbed_raises_when_literal_nbsp_entity_separated_name_survives():
    # Build the precise pre-fix leftover directly, without going through
    # scrub(): a document containing the name separated by the literal
    # '&nbsp;' text, which old-style whitespace-only matching would not
    # have found.
    leftover = f'<span class="username">Alfa{_LITERAL_NBSP_ENTITY}Betova</span>'

    with pytest.raises(AssertionError, match="Alfa Betova"):
        capture_fixture.verify_scrubbed(leftover, ["Alfa Betova"])


def test_scrub_redacts_name_separated_by_literal_decimal_nbsp_entity():
    # '&#160;' double-escaped in the raw markup -> literal '&#160;' text
    # once BeautifulSoup decodes the outer '&amp;'.
    html = '<span title="Alfa&amp;#160;Betova">x</span>'

    cleaned = capture_fixture.scrub(html, ["Alfa Betova"])

    assert "Alfa" not in cleaned
    assert "Betova" not in cleaned
    assert "Osoba 1" in cleaned


def test_verify_scrubbed_raises_when_literal_decimal_nbsp_entity_name_survives():
    leftover = '<span class="username">Alfa&#160;Betova</span>'

    with pytest.raises(AssertionError, match="Alfa Betova"):
        capture_fixture.verify_scrubbed(leftover, ["Alfa Betova"])


@pytest.mark.parametrize("hex_spelling", ["&#xa0;", "&#xA0;", "&#Xa0;", "&#XA0;"])
def test_scrub_redacts_name_separated_by_literal_hex_nbsp_entity_any_case(
    hex_spelling,
):
    # '&#xa0;' (any case combination of the leading 'x' and the hex digit)
    # double-escaped in the raw markup -> the literal hex spelling as text.
    html = f'<span title="Alfa&amp;{hex_spelling[1:]}Betova">x</span>'

    cleaned = capture_fixture.scrub(html, ["Alfa Betova"])

    assert "Alfa" not in cleaned
    assert "Betova" not in cleaned
    assert "Osoba 1" in cleaned


def test_verify_scrubbed_raises_when_literal_hex_nbsp_entity_name_survives():
    leftover = '<span class="username">Alfa&#xA0;Betova</span>'

    with pytest.raises(AssertionError, match="Alfa Betova"):
        capture_fixture.verify_scrubbed(leftover, ["Alfa Betova"])


# --- verify_scrubbed(): normalising check catches variants scrub() doesn't -

# Zero-width space: invisible when rendered, and deliberately NOT added to
# scrub()'s separator pattern (_NAME_SPACE_ENTITIES/_NAME_SPACE_CHARS). This
# stands in for "the next separator variant nobody has thought of yet".
ZERO_WIDTH_SPACE = "​"


def test_scrub_does_not_recognise_zero_width_space_as_a_separator():
    # Documents the deliberate gap: this is not a bug to fix, it is the
    # precondition for the next test to actually prove something.
    html = f"<p>Alfa{ZERO_WIDTH_SPACE}Betova</p>"

    cleaned = capture_fixture.scrub(html, ["Alfa Betova"])

    assert "Osoba 1" not in cleaned
    assert "Alfa" in cleaned
    assert "Betova" in cleaned


def test_verify_scrubbed_raises_on_zero_width_space_separated_name_via_normalisation():
    # The regression test for Part 2: scrub() does not (and, per the task,
    # should not) recognise U+200B as a separator, so this document is
    # exactly what an incomplete scrub() would produce/leave behind. Only
    # the broad, normalising check can catch it - proving verification is
    # genuinely broader than scrub()'s known-variant matching, not a mirror
    # of it.
    leftover = f'<span class="username">Alfa{ZERO_WIDTH_SPACE}Betova</span>'

    assert capture_fixture._name_pattern("Alfa Betova").search(leftover) is None

    with pytest.raises(AssertionError, match="Alfa Betova"):
        capture_fixture.verify_scrubbed(leftover, ["Alfa Betova"])
