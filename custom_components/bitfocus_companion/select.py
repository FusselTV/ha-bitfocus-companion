"""Module update policy for Companion connections."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import CompanionConfigEntry
from .entity import CompanionConnectionEntity
from .helpers import selected_connection_ids, setup_dynamic_entities

PARALLEL_UPDATES = 0

UPDATE_POLICIES = ["manual", "stable", "beta"]

UPDATE_POLICY = SelectEntityDescription(
    key="update_policy",
    translation_key="update_policy",
    entity_category=EntityCategory.CONFIG,
    options=UPDATE_POLICIES,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CompanionConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the update policy select for every selected connection."""
    coordinator = entry.runtime_data
    setup_dynamic_entities(
        coordinator,
        async_add_entities,
        lambda: selected_connection_ids(entry, coordinator.data),
        lambda connection_id: [CompanionUpdatePolicySelect(coordinator, connection_id)],
    )


class CompanionUpdatePolicySelect(CompanionConnectionEntity, SelectEntity):
    """Which module versions Companion may move this connection to."""

    entity_description = UPDATE_POLICY

    @property
    def current_option(self) -> str | None:
        """Return the policy Companion has stored."""
        connection = self.connection
        if connection is None or connection.update_policy not in UPDATE_POLICIES:
            return None
        return connection.update_policy

    async def async_select_option(self, option: str) -> None:
        """Apply a new update policy."""
        await self.coordinator.async_set_connection_update_policy(
            self._connection_id, option
        )
