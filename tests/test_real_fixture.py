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


def test_every_lesson_subject_on_the_real_week_has_a_known_full_name():
    """All nine distinct subjects on the real fixture resolve subject_full,
    including TH (třídnická hodina), whose tooltip alone does not say -
    see MARKER_CLASS_SUBJECT_FULL in api/parser.py.
    """
    entries = parse_week(
        FIXTURE.read_text(encoding="utf-8"), monday=date(2026, 9, 14), tz=PRAGUE
    )
    lessons = [entry for entry in entries if entry.is_lesson]

    subject_full_by_abbrev = {lesson.subject: lesson.subject_full for lesson in lessons}

    assert subject_full_by_abbrev == {
        "M": "Matematika",
        "ČJ": "Český jazyk a literatura",
        "AJ": "Anglický jazyk",
        "PRV": "Prvouka",
        "TV": "Tělesná výchova",
        "HV": "Hudební výchova",
        "PČ": "Pracovní činnosti",
        "VV": "Výtvarná výchova",
        "TH": "Třídnická hodina",
    }
