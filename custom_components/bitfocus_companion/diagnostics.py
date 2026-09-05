"""Diagnostics for Bitfocus Companion."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_TOKEN
from homeassistant.core import HomeAssistant

from .coordinator import CompanionConfigEntry

TO_REDACT = {CONF_TOKEN}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: CompanionConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "companion_version": coordinator.capabilities.version,
        "serves_connections": coordinator.capabilities.connections,
        "last_update_success": coordinator.last_update_success,
        "surfaces": [asdict(surface) for surface in coordinator.data.surfaces.values()],
        "connections": [
            asdict(connection) for connection in coordinator.data.connections.values()
        ],
    }
