"""Identifier helpers shared by the platforms."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_HOST,
    CONF_PORT,
    CONF_SSL,
    CONF_TOKEN,
    CONF_VERIFY_SSL,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from yarl import URL

from .api import CompanionClient, Surface
from .const import CONF_EXCLUDED_CONNECTIONS, CONF_EXCLUDED_SURFACES

if TYPE_CHECKING:
    from .coordinator import CompanionCoordinator, CompanionData


def build_client(hass: HomeAssistant, data: Mapping[str, Any]) -> CompanionClient:
    """Build an API client from config entry data, sharing HA's websession."""
    scheme = "https" if data[CONF_SSL] else "http"
    return CompanionClient(
        async_get_clientsession(hass),
        URL.build(scheme=scheme, host=data[CONF_HOST], port=data[CONF_PORT]),
        data[CONF_TOKEN],
        verify_ssl=data[CONF_VERIFY_SSL],
    )


def surface_name(surface: Surface) -> str:
    """Return the name to show for a surface.

    Companion leaves the name empty until someone sets one, and then only the model
    tells two surfaces apart. The id is on the device page as the serial number.
    """
    return surface.name or surface.type or surface.id


def hub_device_id(entry: ConfigEntry) -> str:
    """Return the device identifier of the Companion instance itself."""
    return entry.entry_id


def surface_device_id(entry: ConfigEntry, surface_id: str) -> str:
    """Return the device identifier of a surface."""
    return f"{entry.entry_id}_surface_{surface_id}"


def connection_device_id(entry: ConfigEntry, connection_id: str) -> str:
    """Return the device identifier of a connection."""
    return f"{entry.entry_id}_connection_{connection_id}"


@callback
def selected_surface_ids(entry: ConfigEntry, data: CompanionData) -> set[str]:
    """Return the surface ids the user wants exposed."""
    excluded = set(entry.options.get(CONF_EXCLUDED_SURFACES, []))
    return {surface_id for surface_id in data.surfaces if surface_id not in excluded}


@callback
def selected_connection_ids(entry: ConfigEntry, data: CompanionData) -> set[str]:
    """Return the connection ids the user wants exposed."""
    excluded = set(entry.options.get(CONF_EXCLUDED_CONNECTIONS, []))
    return {conn_id for conn_id in data.connections if conn_id not in excluded}


@callback
def setup_dynamic_entities(
    coordinator: CompanionCoordinator,
    async_add_entities: AddConfigEntryEntitiesCallback,
    ids: Callable[[], set[str]],
    factory: Callable[[str], Iterable[Entity]],
) -> None:
    """Add entities now and again whenever Companion reports something new."""
    known: set[str] = set()

    @callback
    def _add_new() -> None:
        current = ids()
        # Forget an id only once its entities are really gone. A device survives one
        # missing poll, and adding a second entity for the same unique id while the
        # first still exists makes Home Assistant log an error and drop it.
        known.intersection_update(current | coordinator.object_ids_with_entities)
        new_ids = current - known
        if not new_ids:
            return
        known.update(new_ids)
        async_add_entities(
            [entity for item_id in sorted(new_ids) for entity in factory(item_id)]
        )

    coordinator.config_entry.async_on_unload(coordinator.async_add_listener(_add_new))
    _add_new()
