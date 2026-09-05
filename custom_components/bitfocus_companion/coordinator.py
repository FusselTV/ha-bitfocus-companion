"""Data update coordinator for Bitfocus Companion."""

from __future__ import annotations

import logging
from collections.abc import Coroutine
from dataclasses import dataclass
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    ApiCapabilities,
    CompanionApiUnavailableError,
    CompanionAuthError,
    CompanionClient,
    CompanionError,
    CompanionScopeError,
    Connection,
    Surface,
)
from .const import DOCS_URL, DOMAIN, ISSUE_API_DISABLED

_LOGGER = logging.getLogger(__name__)

type CompanionConfigEntry = ConfigEntry[CompanionCoordinator]


@dataclass(slots=True)
class CompanionData:
    """Everything the integration polls from one Companion instance."""

    surfaces: dict[str, Surface]
    connections: dict[str, Connection]
    # True when this integration may not read connections. An empty list then means
    # "not allowed to look" instead of "there are none", so the devices must stay.
    connections_denied: bool


class CompanionCoordinator(DataUpdateCoordinator[CompanionData]):
    """Poll surfaces and connections from one Companion instance."""

    config_entry: CompanionConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: CompanionConfigEntry,
        client: CompanionClient,
        capabilities: ApiCapabilities,
        scan_interval: timedelta,
    ) -> None:
        """Initialise the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.data[CONF_HOST]}:{entry.data[CONF_PORT]}",
            update_interval=scan_interval,
        )
        self.client = client
        self.capabilities = capabilities
        # Entities grouped by the surface or connection they belong to. A write then
        # refreshes those few entities instead of every entity of the config entry.
        self._object_listeners: dict[str, set[CALLBACK_TYPE]] = {}
        self._connections_denied = False

    async def _async_update_data(self) -> CompanionData:
        """Fetch surfaces, and connections if the token is allowed to see them."""
        try:
            surfaces = await self.client.async_get_surfaces()
            connections, connections_denied = await self._async_get_connections()
        except (CompanionAuthError, CompanionScopeError) as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN,
                translation_key="invalid_auth",
            ) from err
        except CompanionApiUnavailableError as err:
            self._async_create_api_disabled_issue()
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="api_unavailable",
            ) from err
        except CompanionError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
                translation_placeholders={"error": str(err)},
            ) from err

        self._async_clear_api_disabled_issue()
        return CompanionData(
            surfaces={surface.id: surface for surface in surfaces},
            connections={connection.id: connection for connection in connections},
            connections_denied=connections_denied,
        )

    async def _async_get_connections(self) -> tuple[list[Connection], bool]:
        """Return the connections and whether they were out of reach.

        Companion scopes surfaces and connections separately, and versions the two
        resources separately. So either one can be unreachable while the other works.
        """
        if not self.capabilities.connections:
            return [], True
        try:
            connections = await self.client.async_get_connections()
        except (CompanionScopeError, CompanionApiUnavailableError):
            # Either the token may not look, or this Companion dropped the version
            # of the resource this integration uses. Surfaces answered, so the API is
            # up and the rest of the poll is still good.
            return [], True

        return connections, False

    async def async_set_surface_brightness(self, surface_id: str, value: int) -> None:
        """Set surface brightness and write the result straight into the cache."""
        surface = await self._async_call(
            self.client.async_set_surface_brightness(surface_id, value)
        )
        self._async_replace_surface(surface)

    async def async_set_connection_enabled(
        self, connection_id: str, enabled: bool
    ) -> None:
        """Enable or disable a connection."""
        connection = await self._async_call(
            self.client.async_set_connection_enabled(connection_id, enabled)
        )
        self._async_replace_connection(connection)

    async def async_set_connection_update_policy(
        self, connection_id: str, policy: str
    ) -> None:
        """Set the module version update policy of a connection."""
        connection = await self._async_call(
            self.client.async_set_connection_update_policy(connection_id, policy)
        )
        self._async_replace_connection(connection)

    async def async_restart_connection(self, connection_id: str) -> None:
        """Restart a connection."""
        await self._async_call(self.client.async_restart_connection(connection_id))
        await self.async_request_refresh()

    async def _async_call[T](self, coro: Coroutine[None, None, T]) -> T:
        """Run an API call, translating failures into HA errors."""
        try:
            return await coro
        except CompanionAuthError as err:
            # A write is not a poll, so nothing else would notice a dead token.
            self.config_entry.async_start_reauth(self.hass)
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN,
                translation_key="invalid_auth",
            ) from err
        except CompanionScopeError as err:
            # A token that reaches surfaces but not connections is a fine setup. So
            # this is one action that failed, not a broken configuration. Which scope
            # is missing depends on the call, so the message does not name one.
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="insufficient_scope",
            ) from err
        except CompanionApiUnavailableError as err:
            self._async_create_api_disabled_issue()
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="api_unavailable",
            ) from err
        except CompanionError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="api_error",
                translation_placeholders={"error": str(err)},
            ) from err

    @callback
    def async_add_object_listener(
        self, object_id: str, update_callback: CALLBACK_TYPE
    ) -> CALLBACK_TYPE:
        """Subscribe an entity to writes against one surface or connection."""
        listeners = self._object_listeners.setdefault(object_id, set())
        listeners.add(update_callback)

        @callback
        def remove_listener() -> None:
            listeners.discard(update_callback)
            if not listeners:
                self._object_listeners.pop(object_id, None)

        return remove_listener

    @property
    def object_ids_with_entities(self) -> set[str]:
        """Return the surfaces and connections that still have entities."""
        return set(self._object_listeners)

    @callback
    def _async_notify_object(self, object_id: str) -> None:
        """Refresh only the entities of the surface or connection that changed."""
        for update_callback in list(self._object_listeners.get(object_id, ())):
            update_callback()

    def _async_replace_surface(self, surface: Surface) -> None:
        """Publish a single updated surface without waiting for the next poll."""
        self.data.surfaces[surface.id] = surface
        self._async_notify_object(surface.id)

    def _async_replace_connection(self, connection: Connection) -> None:
        """Publish a single updated connection without waiting for the next poll."""
        self.data.connections[connection.id] = connection
        self._async_notify_object(connection.id)

    def _async_create_api_disabled_issue(self) -> None:
        """Tell the user the REST API vanished from a Companion that is still up."""
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            f"{ISSUE_API_DISABLED}_{self.config_entry.entry_id}",
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key=ISSUE_API_DISABLED,
            translation_placeholders={"host": str(self.client.base_url)},
            learn_more_url=DOCS_URL,
        )

    def _async_clear_api_disabled_issue(self) -> None:
        """Drop the issue once the API answers again."""
        ir.async_delete_issue(
            self.hass, DOMAIN, f"{ISSUE_API_DISABLED}_{self.config_entry.entry_id}"
        )
