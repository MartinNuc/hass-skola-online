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
