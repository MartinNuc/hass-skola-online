from datetime import date, datetime
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from homeassistant.components.todo import TodoListEntityFeature
from homeassistant.const import ATTR_SUPPORTED_FEATURES
from homeassistant.core import ServiceCall
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.skola_online.api.exceptions import InvalidAuth
from custom_components.skola_online.api.models import Homework
from custom_components.skola_online.const import (
    CONF_HOMEWORK_TODO,
    DOMAIN,
    EVENT_NEW_HOMEWORK,
)
from custom_components.skola_online.homework import HomeworkCoordinator, todo_item_data

PRAGUE = ZoneInfo("Europe/Prague")
TODO = "todo.shopping_list"


def _hw(task_id: str = "id-1", title: str = "Psaní číslice 2") -> Homework:
    return Homework(
        id=task_id,
        title=title,
        subject="Český jazyk a literatura",
        assigned=datetime(2026, 9, 22, 9, 19, tzinfo=PRAGUE),
        due=datetime(2026, 9, 23, 23, 59, tzinfo=PRAGUE),
        submitted="neodevzdává se",
    )


def test_a_plain_list_gets_the_due_date_in_the_name():
    assert todo_item_data(_hw(), features=0) == {
        "item": "Český jazyk a literatura: Psaní číslice 2 (do 23.9.)"
    }


def test_a_rich_list_gets_due_and_description_in_their_own_fields():
    hw = Homework(**{**_hw().__dict__, "description": "Str. 12"})
    features = (
        TodoListEntityFeature.SET_DUE_DATE_ON_ITEM
        | TodoListEntityFeature.SET_DESCRIPTION_ON_ITEM
    )
    assert todo_item_data(hw, features) == {
        "item": "Český jazyk a literatura: Psaní číslice 2",
        "due_date": date(2026, 9, 23),
        "description": "Str. 12",
    }


def test_due_datetime_is_preferred_when_supported():
    data = todo_item_data(_hw(), TodoListEntityFeature.SET_DUE_DATETIME_ON_ITEM)
    assert data["due_datetime"] == datetime(2026, 9, 23, 23, 59, tzinfo=PRAGUE)


def _setup(hass, homework, todo: str | None = TODO):
    entry = MockConfigEntry(
        domain=DOMAIN, options={CONF_HOMEWORK_TODO: todo} if todo else {}
    )
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.fetch_homework.return_value = homework
    client.fetch_homework_description.return_value = "Str. 12"
    return HomeworkCoordinator(hass, client, "S1#A", config_entry=entry), client


def _fake_todo(hass, fail: bool = False) -> list[ServiceCall]:
    calls: list[ServiceCall] = []
    hass.states.async_set(TODO, "0", {ATTR_SUPPORTED_FEATURES: 0})

    async def add_item(call: ServiceCall) -> None:
        if fail:
            raise HomeAssistantError("nope")
        calls.append(call)

    hass.services.async_register("todo", "add_item", add_item)
    return calls


def _events(hass) -> list:
    events = []
    hass.bus.async_listen(EVENT_NEW_HOMEWORK, events.append)
    return events


async def test_new_homework_is_added_once_and_announced_once(hass):
    calls = _fake_todo(hass)
    events = _events(hass)
    coordinator, client = _setup(hass, [_hw()])

    data = await coordinator._async_update_data()
    await coordinator._async_update_data()
    await hass.async_block_till_done()

    assert data[0].description == "Str. 12"
    assert [c.data["item"] for c in calls] == [
        "Český jazyk a literatura: Psaní číslice 2 (do 23.9.)"
    ]
    assert calls[0].data["entity_id"] == TODO
    assert len(events) == 1
    assert events[0].data["title"] == "Psaní číslice 2"
    assert events[0].data["description"] == "Str. 12"
    # The description is fetched once, not on every poll.
    assert client.fetch_homework_description.await_count == 1


async def test_seen_homework_survives_a_restart(hass):
    calls = _fake_todo(hass)
    first, _ = _setup(hass, [_hw()])
    await first._async_update_data()
    await hass.async_block_till_done()

    second, _ = _setup(hass, [_hw(), _hw("id-2", "Nový")])
    second._store = first._store
    await second._async_update_data()

    assert [c.data["item"].split(": ")[1] for c in calls] == [
        "Psaní číslice 2 (do 23.9.)",
        "Nový (do 23.9.)",
    ]


async def test_a_failed_add_is_retried_on_the_next_poll(hass):
    _fake_todo(hass, fail=True)
    events = _events(hass)
    coordinator, _ = _setup(hass, [_hw()])

    await coordinator._async_update_data()
    await hass.async_block_till_done()
    assert events == []

    hass.services.async_remove("todo", "add_item")
    calls = _fake_todo(hass)
    await coordinator._async_update_data()
    await hass.async_block_till_done()

    assert len(calls) == 1
    assert len(events) == 1


async def test_a_missing_todo_list_is_retried_too(hass):
    coordinator, _ = _setup(hass, [_hw()], todo="todo.gone")
    await coordinator._async_update_data()
    assert coordinator._seen == set()


async def test_without_a_todo_list_only_the_event_fires(hass):
    events = _events(hass)
    coordinator, _ = _setup(hass, [_hw()], todo=None)

    await coordinator._async_update_data()
    await hass.async_block_till_done()

    assert len(events) == 1


async def test_rejected_credentials_ask_for_reauth(hass):
    coordinator, client = _setup(hass, [])
    client.fetch_homework.side_effect = InvalidAuth("nope")

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()
