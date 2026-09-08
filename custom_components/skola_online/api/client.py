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
from .exceptions import CannotConnect, InvalidAuth, SessionExpired
from .models import Child, Entry
from .parser import CHILDREN_SELECT, parse_children, parse_hidden_fields, parse_week

_LOGGER = logging.getLogger(__name__)

APP_HOST = "aplikace.skolaonline.cz"


def calendar_post_data(day: date) -> str:
    """Build the Infragistics calendar's PostData field value.

    The hidden input holds percent-encoded XML - "<x PostData="Y x M x Y x M x
    D x 1"></x>" - where the first pair is the displayed month and the second
    the selected date. The field value is literally the encoded string; the
    form encoder escapes it a second time on the way out, exactly as a browser
    does.
    """
    stamp = f"{day.year}x{day.month}x{day.year}x{day.month}x{day.day}x1"
    return quote(f'<x PostData="{stamp}"></x>', safe="/")


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
        return await self._retry_once_on_expiry(
            lambda: self._fetch_week(monday, child_id)
        )

    async def fetch_week_html(
        self, monday: date, child_id: str | None = None
    ) -> str:
        """Fetch one week's raw calendar HTML, without parsing it.

        Exists so a fixture-capture tool (or any other diagnostic) can get
        the exact page the server returned even when the parser cannot yet
        make sense of it - a capture that only succeeds when parsing also
        succeeds cannot be used to diagnose a parser failure.
        """
        return await self._retry_once_on_expiry(
            lambda: self._fetch_week_html(monday, child_id)
        )

    async def _retry_once_on_expiry(self, call):
        """Run `call()`, retrying once after a fresh login if it expired."""
        try:
            return await call()
        except SessionExpired:
            _LOGGER.debug("session expired, logging in again")
            await self.login()
            return await call()

    async def _fetch_week(
        self, monday: date, child_id: str | None
    ) -> list[Entry]:
        body = await self._fetch_week_html(monday, child_id)
        return parse_week(body, monday=monday, tz=self._tz)

    async def _fetch_week_html(self, monday: date, child_id: str | None) -> str:
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

        return body

    async def list_children(self) -> list[Child]:
        """List the children this account can see.

        A teacher or single-child account may have no dropdown at all, which is
        not an error — it simply means there is nothing to choose.
        """
        page = await self._get_app_page(CALENDAR_URL)
        return parse_children(page)
