"""Restart buttons for Companion connections."""

from __future__ import annotations

from homeassistant.components.button import (
    ButtonDeviceClass,
    ButtonEntity,
    ButtonEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import CompanionConfigEntry
from .entity import CompanionConnectionEntity
from .helpers import selected_connection_ids, setup_dynamic_entities

PARALLEL_UPDATES = 0

CONNECTION_RESTART = ButtonEntityDescription(
    key="restart",
    device_class=ButtonDeviceClass.RESTART,
    entity_category=EntityCategory.CONFIG,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CompanionConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the restart buttons."""
    coordinator = entry.runtime_data
    setup_dynamic_entities(
        coordinator,
        async_add_entities,
        lambda: selected_connection_ids(entry, coordinator.data),
        lambda connection_id: [CompanionRestartButton(coordinator, connection_id)],
    )


class CompanionRestartButton(CompanionConnectionEntity, ButtonEntity):
    """Restart a connection."""

    entity_description = CONNECTION_RESTART

    async def async_press(self) -> None:
        """Restart the connection."""
        await self.coordinator.async_restart_connection(self._connection_id)
