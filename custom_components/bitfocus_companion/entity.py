"""Base entities for Bitfocus Companion."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import Connection, Surface
from .const import DOMAIN
from .coordinator import CompanionCoordinator
from .helpers import (
    connection_device_id,
    hub_device_id,
    surface_device_id,
    surface_name,
)


class CompanionSurfaceEntity(CoordinatorEntity[CompanionCoordinator]):
    """Base entity for a Companion surface."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: CompanionCoordinator,
        surface_id: str,
    ) -> None:
        """Initialise the entity for one surface."""
        super().__init__(coordinator)
        self._surface_id = surface_id
        entry = coordinator.config_entry
        device_id = surface_device_id(entry, surface_id)
        self._attr_unique_id = f"{device_id}_{self.entity_description.key}"

        surface = coordinator.data.surfaces[surface_id]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            name=surface_name(surface),
            manufacturer=surface.integration_type or "Bitfocus",
            model=surface.type or None,
            serial_number=surface_id,
            via_device=(DOMAIN, hub_device_id(entry)),
        )

    async def async_added_to_hass(self) -> None:
        """Also listen for writes against this one surface."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.async_add_object_listener(
                self._surface_id, self._handle_coordinator_update
            )
        )

    @property
    def surface(self) -> Surface | None:
        """Return the surface this entity belongs to, if Companion still has it."""
        return self.coordinator.data.surfaces.get(self._surface_id)

    @property
    def available(self) -> bool:
        """Return whether the surface is still reported by Companion."""
        return super().available and self.surface is not None


class CompanionConnectionEntity(CoordinatorEntity[CompanionCoordinator]):
    """Base entity for a Companion connection."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: CompanionCoordinator,
        connection_id: str,
    ) -> None:
        """Initialise the entity for one connection."""
        super().__init__(coordinator)
        self._connection_id = connection_id
        entry = coordinator.config_entry
        device_id = connection_device_id(entry, connection_id)
        self._attr_unique_id = f"{device_id}_{self.entity_description.key}"

        connection = coordinator.data.connections[connection_id]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            name=connection.label,
            manufacturer="Bitfocus",
            model=connection.module_id or None,
            sw_version=connection.module_version_id,
            via_device=(DOMAIN, hub_device_id(entry)),
        )

    async def async_added_to_hass(self) -> None:
        """Also listen for writes against this one connection."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.async_add_object_listener(
                self._connection_id, self._handle_coordinator_update
            )
        )

    @property
    def connection(self) -> Connection | None:
        """Return the connection this entity belongs to, if it still exists."""
        return self.coordinator.data.connections.get(self._connection_id)

    @property
    def available(self) -> bool:
        """Return whether the connection is still reported by Companion."""
        return super().available and self.connection is not None
