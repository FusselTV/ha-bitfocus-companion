"""System health for Bitfocus Companion."""

from __future__ import annotations

from typing import Any

from homeassistant.components import system_health
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN
from .coordinator import CompanionConfigEntry


@callback
def async_register(
    hass: HomeAssistant, register: system_health.SystemHealthRegistration
) -> None:
    """Register the system health callback."""
    register.async_register_info(system_health_info)


async def system_health_info(hass: HomeAssistant) -> dict[str, Any]:
    """Report how many Companion instances answer, and which versions they run."""
    entries: list[CompanionConfigEntry] = hass.config_entries.async_loaded_entries(
        DOMAIN
    )
    coordinators = [entry.runtime_data for entry in entries]
    versions = sorted(
        {
            coordinator.capabilities.version
            for coordinator in coordinators
            if coordinator.capabilities.version
        }
    )
    return {
        "instances": len(entries),
        "reachable": sum(
            1 for coordinator in coordinators if coordinator.last_update_success
        ),
        "versions": ", ".join(versions) or "unknown",
    }
