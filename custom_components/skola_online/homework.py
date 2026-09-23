"""Polls the homework list and forwards newly assigned tasks.

"New" means a task id this config entry has never seen before, remembered in
a Store so a restart does not re-add everything, and so ticking an item off
(or deleting it) in the to-do list never brings it back.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import timedelta

from homeassistant.components.todo import (
    ATTR_DESCRIPTION,
    ATTR_DUE_DATE,
    ATTR_DUE_DATETIME,
    ATTR_ITEM,
    TodoListEntityFeature,
)
from homeassistant.components.todo import DOMAIN as TODO_DOMAIN
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID, ATTR_SUPPORTED_FEATURES
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api.client import SkolaOnlineClient
from .api.exceptions import CannotConnect, InvalidAuth, ParseError, SessionExpired
from .api.models import Homework
from .const import (
    CONF_HOMEWORK_TODO,
    CONF_SCAN_INTERVAL_HOURS,
    DEFAULT_SCAN_INTERVAL_HOURS,
    DOMAIN,
    EVENT_NEW_HOMEWORK,
)

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1


def todo_item_data(homework: Homework, features: int) -> dict:
    """The todo.add_item service data for one task.

    Due date and description go in their own fields when the list supports
    them. The built-in Shopping List supports neither, and passing a field a
    list doesn't support is an error, so there the due date goes into the
    item's name instead and the description is left out.
    """
    summary = f"{homework.subject}: {homework.title}" if homework.subject else homework.title
    data: dict = {ATTR_ITEM: summary}

    if homework.due is not None:
        if features & TodoListEntityFeature.SET_DUE_DATETIME_ON_ITEM:
            data[ATTR_DUE_DATETIME] = homework.due
        elif features & TodoListEntityFeature.SET_DUE_DATE_ON_ITEM:
            data[ATTR_DUE_DATE] = homework.due.date()
        else:
            due = homework.due
            data[ATTR_ITEM] = f"{summary} (do {due.day}.{due.month}.)"

    if homework.description and features & TodoListEntityFeature.SET_DESCRIPTION_ON_ITEM:
        data[ATTR_DESCRIPTION] = homework.description

    return data


class HomeworkCoordinator(DataUpdateCoordinator[list[Homework]]):
    """Polls the child's open homework on the timetable's interval."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: SkolaOnlineClient,
        child_id: str | None,
        config_entry: ConfigEntry,
    ) -> None:
        options = config_entry.options
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=f"{DOMAIN}_homework",
            update_interval=timedelta(
                hours=options.get(CONF_SCAN_INTERVAL_HOURS, DEFAULT_SCAN_INTERVAL_HOURS)
            ),
        )
        self.client = client
        self.child_id = child_id
        self.todo_entity_id: str | None = options.get(CONF_HOMEWORK_TODO)
        self._store: Store[dict] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.homework.{config_entry.entry_id}"
        )
        self._seen: set[str] | None = None
        # Assignment texts don't change once set, and each costs a request,
        # so fetch each task's once rather than on every poll.
        self._descriptions: dict[str, str | None] = {}

    async def _async_update_data(self) -> list[Homework]:
        try:
            homework = await self.client.fetch_homework(self.child_id)
            homework = [await self._with_description(item) for item in homework]
        except InvalidAuth as err:
            raise ConfigEntryAuthFailed(
                "Škola Online rejected the stored credentials"
            ) from err
        except (CannotConnect, ParseError, SessionExpired) as err:
            raise UpdateFailed(f"could not read homework: {err}") from err

        await self._forward_new(homework)
        return homework

    async def _with_description(self, item: Homework) -> Homework:
        if item.id is None:
            return item
        if item.id not in self._descriptions:
            self._descriptions[item.id] = await self.client.fetch_homework_description(
                item.id
            )
        return replace(item, description=self._descriptions[item.id])

    async def _forward_new(self, homework: list[Homework]) -> None:
        if self._seen is None:
            stored = await self._store.async_load() or {}
            self._seen = set(stored.get("seen", []))

        new = [item for item in homework if item.id and item.id not in self._seen]
        if not new:
            return

        for item in new:
            if self.todo_entity_id and not await self._add_to_todo(item):
                # Neither marked seen nor announced, so the next poll retries
                # it and the event still fires exactly once.
                continue
            self._seen.add(item.id)
            self.hass.bus.async_fire(
                EVENT_NEW_HOMEWORK,
                {
                    "config_entry_id": self.config_entry.entry_id,
                    "id": item.id,
                    "title": item.title,
                    "subject": item.subject,
                    "assigned": item.assigned.isoformat() if item.assigned else None,
                    "due": item.due.isoformat() if item.due else None,
                    "description": item.description,
                },
            )

        await self._store.async_save({"seen": sorted(self._seen)})

    async def _add_to_todo(self, item: Homework) -> bool:
        state = self.hass.states.get(self.todo_entity_id)
        if state is None:
            _LOGGER.warning(
                "to-do list %s not found; homework %r not added",
                self.todo_entity_id,
                item.title,
            )
            return False
        features = state.attributes.get(ATTR_SUPPORTED_FEATURES, 0)
        try:
            await self.hass.services.async_call(
                TODO_DOMAIN,
                "add_item",
                {ATTR_ENTITY_ID: self.todo_entity_id, **todo_item_data(item, features)},
                blocking=True,
            )
        except HomeAssistantError as err:
            _LOGGER.warning(
                "could not add homework %r to %s: %s",
                item.title,
                self.todo_entity_id,
                err,
            )
            return False
        return True
