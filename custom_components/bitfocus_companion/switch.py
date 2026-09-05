"""Enable/disable switches for Companion connections."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import (
    SwitchDeviceClass,
    SwitchEntity,
    SwitchEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import ExtraStoredData, RestoreEntity

from .coordinator import CompanionConfigEntry
from .entity import CompanionConnectionEntity, CompanionSurfaceEntity
from .helpers import (
    selected_connection_ids,
    selected_surface_ids,
    setup_dynamic_entities,
)

PARALLEL_UPDATES = 0

# What to restore to when the screensaver is switched off and nothing was saved -
# a surface left at 0% would otherwise stay dark.
FALLBACK_BRIGHTNESS = 100

# No device class on purpose. The shipped blueprint tells the two kinds of switch
# apart by it, and a screensaver is not an outlet or a plain switch anyway.
SURFACE_SCREENSAVER = SwitchEntityDescription(
    key="screensaver",
    translation_key="screensaver",
)

CONNECTION_ENABLED = SwitchEntityDescription(
    key="enabled",
    translation_key="enabled",
    device_class=SwitchDeviceClass.SWITCH,
    entity_category=EntityCategory.CONFIG,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CompanionConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the surface and connection switches."""
    coordinator = entry.runtime_data
    setup_dynamic_entities(
        coordinator,
        async_add_entities,
        lambda: selected_surface_ids(entry, coordinator.data),
        lambda surface_id: [CompanionScreensaverSwitch(coordinator, surface_id)],
    )
    setup_dynamic_entities(
        coordinator,
        async_add_entities,
        lambda: selected_connection_ids(entry, coordinator.data),
        lambda connection_id: [CompanionConnectionSwitch(coordinator, connection_id)],
    )


@dataclass
class SavedBrightness(ExtraStoredData):
    """The brightness to come back to after the screensaver."""

    brightness: int

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable representation."""
        return {"brightness": self.brightness}


class CompanionScreensaverSwitch(CompanionSurfaceEntity, SwitchEntity, RestoreEntity):
    """Dim a surface to nothing, and put it back where it was.

    On is defined as "the surface is at 0%", so dimming it to zero anywhere else -
    the brightness entity, Companion's own UI - shows up here too.
    """

    entity_description = SURFACE_SCREENSAVER
    _saved_brightness: int | None = None

    async def async_added_to_hass(self) -> None:
        """Recover the saved brightness across a restart."""
        await super().async_added_to_hass()
        if (stored := await self.async_get_last_extra_data()) is not None:
            saved = stored.as_dict().get("brightness")
            if isinstance(saved, int):
                self._saved_brightness = saved
        self._remember_brightness()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Keep track of the last brightness the surface was actually lit at."""
        self._remember_brightness()
        super()._handle_coordinator_update()

    @callback
    def _remember_brightness(self) -> None:
        """Remember where to come back to, however the surface got dimmed.

        Brightness can reach 0 through the number entity or through Companion's own
        UI, and both should be reversible to the value they came from.
        """
        surface = self.surface
        if surface is not None and surface.brightness:
            self._saved_brightness = surface.brightness

    @property
    def extra_restore_state_data(self) -> ExtraStoredData | None:
        """Persist the saved brightness alongside the state."""
        if self._saved_brightness is None:
            return None
        return SavedBrightness(self._saved_brightness)

    @property
    def is_on(self) -> bool | None:
        """Return whether the surface is currently dark."""
        surface = self.surface
        if surface is None or surface.brightness is None:
            return None
        return surface.brightness == 0

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Remember the current brightness and dim the surface to nothing."""
        self._remember_brightness()
        await self.coordinator.async_set_surface_brightness(self._surface_id, 0)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Put the brightness back where it was."""
        await self.coordinator.async_set_surface_brightness(
            self._surface_id, self._saved_brightness or FALLBACK_BRIGHTNESS
        )


class CompanionConnectionSwitch(CompanionConnectionEntity, SwitchEntity):
    """Whether a connection is enabled in Companion."""

    entity_description = CONNECTION_ENABLED

    @property
    def is_on(self) -> bool | None:
        """Return whether the connection is enabled."""
        connection = self.connection
        return None if connection is None else connection.enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable the connection."""
        await self.coordinator.async_set_connection_enabled(self._connection_id, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable the connection."""
        await self.coordinator.async_set_connection_enabled(self._connection_id, False)
