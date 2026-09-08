from zoneinfo import ZoneInfo

import aiohttp
import pytest
from aioresponses import aioresponses

from custom_components.skola_online.api.client import SkolaOnlineClient
from custom_components.skola_online.const import CALENDAR_URL

PRAGUE = ZoneInfo("Europe/Prague")

PAGE = (
    "<html><body>"
    '<select name="listOfChildrenPart$listOfChildren$DDLChildren">'
    '<option value="S001#D1">Dítě Jedno</option>'
    '<option value="S001#D2">Dítě Druhé</option>'
    "</select></body></html>"
)


@pytest.fixture
async def client():
    async with aiohttp.ClientSession() as session:
        yield SkolaOnlineClient("user", "pass", session, PRAGUE)


async def test_list_children_reads_the_dropdown(client):
    with aioresponses() as mocked:
        mocked.get(CALENDAR_URL, status=200, body=PAGE)

        children = await client.list_children()

    assert [(c.id, c.name) for c in children] == [
        ("S001#D1", "Dítě Jedno"),
        ("S001#D2", "Dítě Druhé"),
    ]


async def test_list_children_is_empty_when_the_account_has_no_dropdown(client):
    with aioresponses() as mocked:
        mocked.get(CALENDAR_URL, status=200, body="<html><body></body></html>")

        assert await client.list_children() == []
