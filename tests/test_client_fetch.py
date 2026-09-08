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

    # aioresponses keys mocked.requests by (method, url); the GET used to
    # harvest hidden fields lands under a different key than the POST, so we
    # must pick the POST call specifically rather than grab "the first call
    # recorded" (which would be the GET).
    post_calls = [
        calls
        for (method, url), calls in mocked.requests.items()
        if method == "POST" and str(url) == CALENDAR_URL
    ]
    assert len(post_calls) == 1
    sent = post_calls[0][-1].kwargs["data"]

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


async def test_fetch_week_html_returns_the_posts_raw_body(client):
    """A fixture-capture tool needs the exact page, not parsed Entry objects."""
    with aioresponses() as mocked:
        mocked.get(CALENDAR_URL, status=200, body=_week_page())
        mocked.post(CALENDAR_URL, status=200, body=_week_page())

        html = await client.fetch_week_html(date(2026, 9, 14), child_id="S001#D1")

    assert html == _week_page()

    post_calls = [
        calls
        for (method, url), calls in mocked.requests.items()
        if method == "POST" and str(url) == CALENDAR_URL
    ]
    assert len(post_calls) == 1
    sent = post_calls[0][-1].kwargs["data"]
    assert sent["calendarPart_kalendar"] == calendar_post_data(date(2026, 9, 14))
    assert sent["listOfChildrenPart$listOfChildren$DDLChildren"] == "S001#D1"


async def test_fetch_week_html_relogs_in_once_when_the_session_has_expired(client):
    """Mirrors fetch_week's retry-once behaviour, but returns raw HTML."""
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

        html = await client.fetch_week_html(date(2026, 9, 14))

    assert html == _week_page()
