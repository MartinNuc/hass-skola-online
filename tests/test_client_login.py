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
