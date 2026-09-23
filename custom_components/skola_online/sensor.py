"""Exposes a child's open homework as a sensor."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import SkolaOnlineConfigEntry
from .const import DOMAIN
from .homework import HomeworkCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SkolaOnlineConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the homework sensor for this config entry."""
    async_add_entities([HomeworkSensor(entry.runtime_data.homework, entry)])


class HomeworkSensor(CoordinatorEntity[HomeworkCoordinator], SensorEntity):
    """How many tasks the homework list currently shows, and what they are."""

    _attr_has_entity_name = True
    _attr_translation_key = "homework"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:notebook-edit-outline"

    def __init__(self, coordinator: HomeworkCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_homework"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Škola Online",
        )

    @property
    def native_value(self) -> int | None:
        if self.coordinator.data is None:
            return None
        return len(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "homework": [
                {
                    "title": item.title,
                    "subject": item.subject,
                    "assigned": item.assigned.isoformat() if item.assigned else None,
                    "due": item.due.isoformat() if item.due else None,
                    "submitted": item.submitted,
                    "description": item.description,
                }
                for item in sorted(
                    self.coordinator.data or [],
                    key=lambda item: (item.due is None, item.due or 0),
                )
            ]
        }
