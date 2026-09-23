from zoneinfo import ZoneInfo

import aiohttp
import pytest
from aioresponses import aioresponses

from custom_components.skola_online.api.client import SkolaOnlineClient
from custom_components.skola_online.api.parser import HOMEWORK_CHILDREN_SELECT
from custom_components.skola_online.const import HOMEWORK_DETAIL_URL, HOMEWORK_URL

from .test_homework_parser import TASK_ID, _page, _row

PRAGUE = ZoneInfo("Europe/Prague")


def _children(selected: str) -> str:
    options = "".join(
        f'<option value="{v}"{" selected" if v == selected else ""}>{v}</option>'
        for v in ("S1#A", "S1#B")
    )
    return f'<select name="{HOMEWORK_CHILDREN_SELECT}">{options}</select>'


def _posts(mocked):
    return [
        call
        for (method, url), calls in mocked.requests.items()
        if method == "POST" and str(url) == HOMEWORK_URL
        for call in calls
    ]


@pytest.fixture
async def client():
    async with aiohttp.ClientSession() as session:
        yield SkolaOnlineClient("user", "pass", session, PRAGUE)


async def test_the_selected_child_is_read_with_a_plain_get(client):
    page = _page(_row("A", "Prvouka", "", "", ""), children=_children("S1#A"))
    with aioresponses() as mocked:
        mocked.get(HOMEWORK_URL, status=200, body=page)

        items = await client.fetch_homework("S1#A")

        assert _posts(mocked) == []
    assert [i.title for i in items] == ["A"]


async def test_another_child_is_switched_to_by_auto_postback(client):
    before = _page(_row("A", "Prvouka", "", "", ""), children=_children("S1#A"))
    after = _page(_row("B", "Prvouka", "", "", ""), children=_children("S1#B"))
    with aioresponses() as mocked:
        mocked.get(HOMEWORK_URL, status=200, body=before)
        mocked.post(HOMEWORK_URL, status=200, body=after)

        items = await client.fetch_homework("S1#B")

        [post] = _posts(mocked)
    sent = post.kwargs["data"]
    assert sent["__EVENTTARGET"] == HOMEWORK_CHILDREN_SELECT
    assert sent[HOMEWORK_CHILDREN_SELECT] == "S1#B"
    assert sent["__VIEWSTATE"] == "vs"
    assert [i.title for i in items] == ["B"]


async def test_description_is_fetched_by_task_id(client):
    detail = (
        '<span id="ctl00_main_uwt__ctl0_lblPodrobneZadaniValue"><p>Str. 12</p></span>'
    )
    with aioresponses() as mocked:
        mocked.get(f"{HOMEWORK_DETAIL_URL}?UkolID={TASK_ID}", status=200, body=detail)

        assert await client.fetch_homework_description(TASK_ID) == "Str. 12"
