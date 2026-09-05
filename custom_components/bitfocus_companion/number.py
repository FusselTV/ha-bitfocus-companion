"""Brightness control for Companion surfaces."""

from __future__ import annotations

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import CompanionConfigEntry
from .entity import CompanionSurfaceEntity
from .helpers import selected_surface_ids, setup_dynamic_entities

PARALLEL_UPDATES = 0

BRIGHTNESS = NumberEntityDescription(
    key="brightness",
    translation_key="brightness",
    entity_category=EntityCategory.CONFIG,
    native_min_value=0,
    native_max_value=100,
    native_step=1,
    native_unit_of_measurement=PERCENTAGE,
    mode=NumberMode.SLIDER,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CompanionConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the brightness number for every selected surface."""
    coordinator = entry.runtime_data
    setup_dynamic_entities(
        coordinator,
        async_add_entities,
        lambda: selected_surface_ids(entry, coordinator.data),
        lambda surface_id: [CompanionBrightnessNumber(coordinator, surface_id)],
    )


class CompanionBrightnessNumber(CompanionSurfaceEntity, NumberEntity):
    """Brightness of a surface, in percent."""

    entity_description = BRIGHTNESS

    @property
    def native_value(self) -> float | None:
        """Return the brightness Companion has stored for this surface."""
        surface = self.surface
        return None if surface is None else surface.brightness

    async def async_set_native_value(self, value: float) -> None:
        """Set the brightness of the surface."""
        await self.coordinator.async_set_surface_brightness(
            self._surface_id, int(value)
        )
