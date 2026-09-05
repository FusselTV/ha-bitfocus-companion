"""Binary sensors for Companion surfaces and connections."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
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

SURFACE_CONNECTED = BinarySensorEntityDescription(
    key="connected",
    device_class=BinarySensorDeviceClass.CONNECTIVITY,
    entity_category=EntityCategory.DIAGNOSTIC,
)

CONNECTION_PROBLEM = BinarySensorEntityDescription(
    key="problem",
    device_class=BinarySensorDeviceClass.PROBLEM,
    entity_category=EntityCategory.DIAGNOSTIC,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CompanionConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the binary sensors."""
    coordinator = entry.runtime_data
    setup_dynamic_entities(
        coordinator,
        async_add_entities,
        lambda: selected_surface_ids(entry, coordinator.data),
        lambda surface_id: [CompanionSurfaceConnected(coordinator, surface_id)],
    )
    setup_dynamic_entities(
        coordinator,
        async_add_entities,
        lambda: selected_connection_ids(entry, coordinator.data),
        lambda connection_id: [CompanionConnectionProblem(coordinator, connection_id)],
    )


class CompanionSurfaceConnected(CompanionSurfaceEntity, BinarySensorEntity):
    """Whether a surface is currently plugged in."""

    entity_description = SURFACE_CONNECTED

    @property
    def is_on(self) -> bool | None:
        """Return whether Companion currently sees the surface."""
        surface = self.surface
        return None if surface is None else surface.is_connected


class CompanionConnectionProblem(CompanionConnectionEntity, BinarySensorEntity):
    """Whether a connection reports a warning or an error."""

    entity_description = CONNECTION_PROBLEM

    @property
    def is_on(self) -> bool | None:
        """Return whether the connection status is anything but good."""
        connection = self.connection
        if connection is None:
            return None
        if not connection.enabled:
            return False
        if connection.status is None or connection.status.category is None:
            return None
        return connection.status.category != "good"
