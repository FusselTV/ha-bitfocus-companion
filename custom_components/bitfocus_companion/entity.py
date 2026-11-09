"""Base entities for Bitfocus Companion."""

from __future__ import annotations

from homeassistant.helpers import device_registry as dr
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


class _CompanionEntity(CoordinatorEntity[CompanionCoordinator]):
    """Base entity for one surface or connection, a device under the hub."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: CompanionCoordinator, object_id: str, device_id: str
    ) -> None:
        """Set the unique id and remember which object this entity follows."""
        super().__init__(coordinator)
        self._object_id = object_id
        self._attr_unique_id = f"{device_id}_{self.entity_description.key}"

    def _hub_device_id(self) -> str:
        """Return the registry id of the Companion instance device."""
        entry = self.coordinator.config_entry
        return dr.async_get_device_id_by_identifier(
            self.coordinator.hass,
            (DOMAIN, hub_device_id(entry)),
            config_entry_id=entry.entry_id,
        )

    async def async_added_to_hass(self) -> None:
        """Also listen for writes against this one object."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.async_add_object_listener(
                self._object_id, self._handle_coordinator_update
            )
        )


class CompanionSurfaceEntity(_CompanionEntity):
    """Base entity for a Companion surface."""

    def __init__(self, coordinator: CompanionCoordinator, surface_id: str) -> None:
        """Initialise the entity for one surface."""
        device_id = surface_device_id(coordinator.config_entry, surface_id)
        super().__init__(coordinator, surface_id, device_id)
        self._surface_id = surface_id
        surface = coordinator.data.surfaces[surface_id]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            name=surface_name(surface),
            manufacturer=surface.integration_type or "Bitfocus",
            model=surface.type or None,
            serial_number=surface_id,
            via_device_id=self._hub_device_id(),
        )

    @property
    def surface(self) -> Surface | None:
        """Return the surface this entity belongs to, if Companion still has it."""
        return self.coordinator.data.surfaces.get(self._surface_id)

    @property
    def available(self) -> bool:
        """Return whether the surface is still reported by Companion."""
        return super().available and self.surface is not None


class CompanionConnectionEntity(_CompanionEntity):
    """Base entity for a Companion connection."""

    def __init__(self, coordinator: CompanionCoordinator, connection_id: str) -> None:
        """Initialise the entity for one connection."""
        device_id = connection_device_id(coordinator.config_entry, connection_id)
        super().__init__(coordinator, connection_id, device_id)
        self._connection_id = connection_id
        connection = coordinator.data.connections[connection_id]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            name=connection.label,
            manufacturer="Bitfocus",
            model=connection.module_id or None,
            sw_version=connection.module_version_id,
            via_device_id=self._hub_device_id(),
        )

    @property
    def connection(self) -> Connection | None:
        """Return the connection this entity belongs to, if it still exists."""
        return self.coordinator.data.connections.get(self._connection_id)

    @property
    def available(self) -> bool:
        """Return whether the connection is still reported by Companion."""
        return super().available and self.connection is not None
