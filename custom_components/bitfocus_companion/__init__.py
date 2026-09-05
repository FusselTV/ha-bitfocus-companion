"""The Bitfocus Companion integration."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import timedelta
from typing import TypedDict

from homeassistant.const import CONF_SCAN_INTERVAL, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir

from .api import (
    CompanionApiUnavailableError,
    CompanionApiVersionError,
    CompanionConnectionError,
    CompanionNotFoundError,
)
from .const import (
    CONF_EXCLUDED_CONNECTIONS,
    CONF_EXCLUDED_SURFACES,
    DEFAULT_SCAN_INTERVAL,
    DOCS_URL,
    DOMAIN,
    ISSUE_API_DISABLED,
    ISSUE_CONNECTIONS_SCOPE_LOST,
)
from .coordinator import CompanionConfigEntry, CompanionCoordinator
from .helpers import (
    build_client,
    connection_device_id,
    hub_device_id,
    selected_connection_ids,
    selected_surface_ids,
    surface_device_id,
    surface_name,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]


async def async_setup_entry(hass: HomeAssistant, entry: CompanionConfigEntry) -> bool:
    """Set up Bitfocus Companion from a config entry."""
    client = build_client(hass, dict(entry.data))
    try:
        capabilities = await client.async_get_capabilities()
    except CompanionApiVersionError as err:
        raise ConfigEntryError(
            translation_domain=DOMAIN,
            translation_key="unsupported_api",
            translation_placeholders={"version": err.version, "resource": err.missing},
        ) from err
    except CompanionApiUnavailableError as err:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN, translation_key="api_unavailable"
        ) from err
    except (CompanionConnectionError, CompanionNotFoundError) as err:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="cannot_connect",
            translation_placeholders={"error": str(err)},
        ) from err

    coordinator = CompanionCoordinator(
        hass,
        entry,
        client,
        capabilities,
        timedelta(seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)),
    )

    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    _async_register_hub_device(hass, entry)
    sync_devices = _device_sync_callback(hass, entry)
    entry.async_on_unload(coordinator.async_add_listener(sync_devices))
    # Run it once here too. A device the user unticked before this reload should go
    # now, not at the next poll.
    sync_devices()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: CompanionConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: CompanionConfigEntry) -> None:
    """Drop the repair issues raised for an entry that no longer exists."""
    for issue in (ISSUE_API_DISABLED, ISSUE_CONNECTIONS_SCOPE_LOST):
        ir.async_delete_issue(hass, DOMAIN, f"{issue}_{entry.entry_id}")


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: CompanionConfigEntry, device: dr.DeviceEntry
) -> bool:
    """Allow deleting a device that is gone from Companion or was unticked."""
    if not hasattr(entry, "runtime_data"):
        # The entry is unloaded or failed to set up, so nothing here is in use.
        return True
    data = entry.runtime_data.data
    wanted = {hub_device_id(entry)} | {
        surface_device_id(entry, surface_id)
        for surface_id in selected_surface_ids(entry, data)
    }
    if not data.connections_denied:
        # While connections are out of reach their devices are kept on purpose, and
        # the repair issue tells the user so. Deleting one by hand stays allowed.
        wanted |= {
            connection_device_id(entry, connection_id)
            for connection_id in selected_connection_ids(entry, data)
        }
    return not any(identifier[1] in wanted for identifier in device.identifiers)


@callback
def _async_register_hub_device(
    hass: HomeAssistant, entry: CompanionConfigEntry
) -> None:
    """Register the Companion instance itself, so surfaces can hang off it."""
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, hub_device_id(entry))},
        manufacturer="Bitfocus",
        name=entry.title,
        model="Companion",
        sw_version=entry.runtime_data.capabilities.version,
        configuration_url=str(entry.runtime_data.client.base_url),
    )


@callback
def _device_sync_callback(
    hass: HomeAssistant, entry: CompanionConfigEntry
) -> Callable[[], None]:
    """Build the listener that keeps the device registry in step with Companion."""
    # A device that Companion stops reporting must be missing from two polls in a row
    # before it goes. Companion can answer with a short list while it is still
    # starting up, and deleting a device throws away its area, its name and every
    # automation that uses its entities. Unticking one in the options is a clear
    # decision, so those go right away.
    missing_last_time: set[str] = set()

    @callback
    def _sync() -> None:
        wanted = _wanted_device_ids(hass, entry)
        reported = _reported_device_ids(hass, entry)
        names = _device_names(entry)
        registry = dr.async_get(hass)
        missing_now: set[str] = set()
        orphaned_connections = False

        for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
            own_ids = {identifier[1] for identifier in device.identifiers}
            if not own_ids & wanted:
                if own_ids & reported:
                    # Companion still reports it. The user just does not want it.
                    registry.async_update_device(
                        device.id, remove_config_entry_id=entry.entry_id
                    )
                    continue
                missing_now |= own_ids
                if own_ids & missing_last_time:
                    registry.async_update_device(
                        device.id, remove_config_entry_id=entry.entry_id
                    )
                continue

            own_id = next(iter(own_ids & wanted))
            if entry.runtime_data.data.connections_denied and own_id.startswith(
                f"{entry.entry_id}_connection_"
            ):
                # Kept only because the token cannot look. Its entities are unavailable.
                orphaned_connections = True

            if (update := names.get(own_id)) is not None:
                registry.async_update_device(device.id, **update)

        missing_last_time.clear()
        missing_last_time.update(missing_now)
        _async_sync_scope_issue(hass, entry, orphaned_connections)

    return _sync


@callback
def _async_sync_scope_issue(
    hass: HomeAssistant, entry: CompanionConfigEntry, orphaned: bool
) -> None:
    """Report connection devices that the token can no longer see.

    The registry is what makes this durable: a reload resets the coordinator, but
    devices left behind by a narrowed token are still sitting there.
    """
    issue_id = f"{ISSUE_CONNECTIONS_SCOPE_LOST}_{entry.entry_id}"
    if not orphaned:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_CONNECTIONS_SCOPE_LOST,
        translation_placeholders={"host": str(entry.runtime_data.client.base_url)},
        learn_more_url=DOCS_URL,
    )


class _DeviceNaming(TypedDict, total=False):
    """Registry fields that follow whatever Companion currently reports."""

    name: str
    model: str | None
    sw_version: str | None


@callback
def _device_names(entry: CompanionConfigEntry) -> dict[str, _DeviceNaming]:
    """Return the registry fields that follow whatever Companion reports."""
    data = entry.runtime_data.data
    names: dict[str, _DeviceNaming] = {
        surface_device_id(entry, surface.id): _DeviceNaming(
            name=surface_name(surface), model=surface.type or None
        )
        for surface in data.surfaces.values()
    }
    for connection in data.connections.values():
        names[connection_device_id(entry, connection.id)] = _DeviceNaming(
            name=connection.label,
            model=connection.module_id or None,
            sw_version=connection.module_version_id,
        )
    return names


@callback
def _reported_device_ids(hass: HomeAssistant, entry: CompanionConfigEntry) -> set[str]:
    """Return the devices Companion is reporting, whether or not they are wanted."""
    data = entry.runtime_data.data
    reported = {hub_device_id(entry)} | {
        surface_device_id(entry, surface_id) for surface_id in data.surfaces
    }
    if data.connections_denied:
        # Nothing here says they are gone, so treat every connection device as present.
        reported |= _known_connection_device_ids(hass, entry)
    else:
        reported |= {
            connection_device_id(entry, connection_id)
            for connection_id in data.connections
        }
    return reported


@callback
def _known_connection_device_ids(
    hass: HomeAssistant, entry: CompanionConfigEntry
) -> set[str]:
    """Return the connection devices already in the registry for this entry."""
    return {
        identifier[1]
        for device in dr.async_entries_for_config_entry(
            dr.async_get(hass), entry.entry_id
        )
        for identifier in device.identifiers
        if identifier[1].startswith(f"{entry.entry_id}_connection_")
    }


@callback
def _wanted_device_ids(hass: HomeAssistant, entry: CompanionConfigEntry) -> set[str]:
    """Return the devices that should exist: reported by Companion and not unticked."""
    unticked = {
        surface_device_id(entry, surface_id)
        for surface_id in entry.options.get(CONF_EXCLUDED_SURFACES, [])
    } | {
        connection_device_id(entry, connection_id)
        for connection_id in entry.options.get(CONF_EXCLUDED_CONNECTIONS, [])
    }
    return _reported_device_ids(hass, entry) - unticked
