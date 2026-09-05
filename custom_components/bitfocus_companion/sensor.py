"""Sensors for Companion surfaces and connections."""

from __future__ import annotations

from typing import Any, Final

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import CompanionConfigEntry
from .entity import CompanionConnectionEntity, CompanionSurfaceEntity
from .helpers import (
    selected_connection_ids,
    selected_surface_ids,
    setup_dynamic_entities,
)

PARALLEL_UPDATES = 0

# Companion maps every module status onto one of these. Anything it adds later shows
# up as "unknown" instead of breaking the sensor.
STATUS_CATEGORIES: Final = ["good", "warning", "error", "unknown"]

SURFACE_PAGE = SensorEntityDescription(
    key="page",
    translation_key="page",
)

CONNECTION_STATUS = SensorEntityDescription(
    key="status",
    translation_key="status",
    device_class=SensorDeviceClass.ENUM,
    options=STATUS_CATEGORIES,
)

CONNECTION_STATUS_MESSAGE = SensorEntityDescription(
    key="status_message",
    translation_key="status_message",
    entity_category=EntityCategory.DIAGNOSTIC,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CompanionConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors."""
    coordinator = entry.runtime_data
    setup_dynamic_entities(
        coordinator,
        async_add_entities,
        lambda: selected_surface_ids(entry, coordinator.data),
        lambda surface_id: [CompanionSurfacePageSensor(coordinator, surface_id)],
    )
    setup_dynamic_entities(
        coordinator,
        async_add_entities,
        lambda: selected_connection_ids(entry, coordinator.data),
        lambda connection_id: [
            CompanionConnectionStatusSensor(coordinator, connection_id),
            CompanionConnectionStatusMessageSensor(coordinator, connection_id),
        ],
    )


class CompanionSurfacePageSensor(CompanionSurfaceEntity, SensorEntity):
    """The page a surface is currently showing."""

    entity_description = SURFACE_PAGE

    @property
    def native_value(self) -> int | None:
        """Return the page number the surface is showing."""
        surface = self.surface
        if surface is None or surface.page is None:
            return None
        return surface.page.number

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the page name and id alongside the number."""
        surface = self.surface
        if surface is None or surface.page is None:
            return None
        return {"page_id": surface.page.id, "page_name": surface.page.name}


class CompanionConnectionStatusSensor(CompanionConnectionEntity, SensorEntity):
    """Status category reported by a connection module."""

    entity_description = CONNECTION_STATUS

    @property
    def native_value(self) -> str | None:
        """Return the status category, clamped to the known options."""
        connection = self.connection
        if connection is None or connection.status is None:
            return None
        category = connection.status.category
        if category is None:
            return None
        return category if category in STATUS_CATEGORIES else "unknown"


class CompanionConnectionStatusMessageSensor(CompanionConnectionEntity, SensorEntity):
    """Status message reported by a connection module."""

    entity_description = CONNECTION_STATUS_MESSAGE

    @property
    def native_value(self) -> str | None:
        """Return the status message, truncated to what HA can store."""
        connection = self.connection
        if connection is None or connection.status is None:
            return None
        message = connection.status.message
        return None if message is None else message[:255]

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the raw status level from the module."""
        connection = self.connection
        if connection is None or connection.status is None:
            return None
        return {"level": connection.status.level}
