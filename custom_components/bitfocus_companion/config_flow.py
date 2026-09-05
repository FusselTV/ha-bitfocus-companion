"""Config flow for Bitfocus Companion."""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    SOURCE_ZEROCONF,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import (
    CONF_HOST,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONF_SSL,
    CONF_TOKEN,
    CONF_VERIFY_SSL,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from yarl import URL

from .api import (
    CompanionApiUnavailableError,
    CompanionApiVersionError,
    CompanionAuthError,
    CompanionConnectionError,
    CompanionError,
    CompanionNotFoundError,
    CompanionScopeError,
    Connection,
    Surface,
)
from .const import (
    CONF_EXCLUDED_CONNECTIONS,
    CONF_EXCLUDED_SURFACES,
    CONF_MACHINE_ID,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TOKEN,
    DOCS_URL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .helpers import build_client

_LOGGER = logging.getLogger(__name__)

REST_API_FLAG = "EXPERIMENTAL_ENABLE_REST_API"

FIELD_SURFACES = "surfaces"
FIELD_CONNECTIONS = "connections"

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): TextSelector(),
        vol.Required(CONF_PORT, default=DEFAULT_PORT): NumberSelector(
            NumberSelectorConfig(min=1, max=65535, mode=NumberSelectorMode.BOX, step=1)
        ),
        vol.Required(CONF_TOKEN, default=DEFAULT_TOKEN): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
        vol.Required(CONF_SSL, default=False): BooleanSelector(),
        vol.Required(CONF_VERIFY_SSL, default=True): BooleanSelector(),
    }
)

STEP_TOKEN_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_TOKEN): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        )
    }
)


@dataclass(slots=True)
class ValidationResult:
    """What a successful credential check found on the other end."""

    version: str
    surfaces: list[Surface]
    connections: list[Connection]


@dataclass(slots=True)
class ValidationFailure:
    """A failed credential check, ready to hand to async_show_form."""

    errors: dict[str, str]
    placeholders: dict[str, str]


async def async_validate_input(
    hass: HomeAssistant, data: Mapping[str, Any]
) -> ValidationResult | ValidationFailure:
    """Check host, port and token, and fetch what is there to expose.

    Every failure mode gets its own message: an unreachable host, something that is
    not Companion, a Companion without the experimental REST API, one that is too
    old, a bad token, and a token whose scopes are too narrow all look identical from
    the outside otherwise.
    """
    client = build_client(hass, data)
    connections: list[Connection] = []
    try:
        capabilities = await client.async_get_capabilities()
        surfaces = await client.async_get_surfaces()
        if capabilities.connections:
            # Companion scopes surfaces and connections separately, so a token that
            # only reaches surfaces is a narrower setup, not a broken one.
            with suppress(CompanionScopeError):
                connections = await client.async_get_connections()
    except CompanionApiVersionError as err:
        return ValidationFailure(
            {"base": "unsupported_api"},
            {"version": err.version, "resource": err.missing},
        )
    except CompanionApiUnavailableError:
        return ValidationFailure({"base": "api_unavailable"}, {"flag": REST_API_FLAG})
    except CompanionNotFoundError:
        return ValidationFailure({CONF_HOST: "not_companion"}, {})
    except CompanionConnectionError as err:
        return ValidationFailure({CONF_HOST: "cannot_connect"}, {"error": str(err)})
    except CompanionAuthError:
        return ValidationFailure({CONF_TOKEN: "invalid_auth"}, {})
    except CompanionScopeError:
        return ValidationFailure({CONF_TOKEN: "insufficient_scope"}, {})
    except CompanionError as err:
        return ValidationFailure({"base": "unknown"}, {"error": str(err)})
    except Exception as err:
        _LOGGER.exception("Unexpected error while checking the Companion connection")
        return ValidationFailure({"base": "unknown"}, {"error": str(err)})

    return ValidationResult(capabilities.version, surfaces, connections)


@callback
def _normalise_host(user_input: dict[str, Any]) -> None:
    """Take what the browser address bar shows, not just a bare hostname.

    A pasted "http://companion:8000/" would otherwise reach yarl as a host and raise,
    which leaves the dialog with nothing to say.
    """
    raw = str(user_input[CONF_HOST]).strip()
    parsed = URL(raw if "//" in raw else f"//{raw}")
    if parsed.host:
        user_input[CONF_HOST] = parsed.host
    if parsed.explicit_port is not None:
        user_input[CONF_PORT] = parsed.explicit_port
    if parsed.scheme in ("http", "https"):
        user_input[CONF_SSL] = parsed.scheme == "https"


def _picker(options: list[SelectOptionDict]) -> SelectSelector:
    """Build a multi-select list of devices."""
    return SelectSelector(
        SelectSelectorConfig(
            options=options, multiple=True, mode=SelectSelectorMode.LIST
        )
    )


def _device_schema(
    surfaces: list[Surface],
    connections: list[Connection],
    *,
    excluded_surfaces: set[str],
    excluded_connections: set[str],
) -> vol.Schema:
    """Build the "which devices do you want" form."""
    schema: dict[Any, Any] = {}
    if surfaces:
        default = [
            surface.id for surface in surfaces if surface.id not in excluded_surfaces
        ]
        schema[vol.Optional(FIELD_SURFACES, default=default)] = _picker(
            [
                SelectOptionDict(value=surface.id, label=surface.display_name)
                for surface in surfaces
            ]
        )
    if connections:
        default = [
            connection.id
            for connection in connections
            if connection.id not in excluded_connections
        ]
        schema[vol.Optional(FIELD_CONNECTIONS, default=default)] = _picker(
            [
                SelectOptionDict(
                    value=connection.id,
                    label=f"{connection.label} ({connection.module_id})",
                )
                for connection in connections
            ]
        )
    return vol.Schema(schema)


def _invert_selection(
    user_input: Mapping[str, Any],
    surfaces: list[Surface],
    connections: list[Connection],
    stored: Mapping[str, Any],
) -> dict[str, list[str]]:
    """Turn the picked devices into the exclusion lists that get stored.

    Only ids the form actually offered may lose their exclusion. Anything Companion
    did not report this time keeps whatever the user decided about it earlier.
    """
    return {
        CONF_EXCLUDED_SURFACES: _merge_exclusions(
            stored.get(CONF_EXCLUDED_SURFACES, []),
            offered=[surface.id for surface in surfaces],
            picked=user_input.get(FIELD_SURFACES, []),
        ),
        CONF_EXCLUDED_CONNECTIONS: _merge_exclusions(
            stored.get(CONF_EXCLUDED_CONNECTIONS, []),
            offered=[connection.id for connection in connections],
            picked=user_input.get(FIELD_CONNECTIONS, []),
        ),
    }


def _merge_exclusions(
    stored: Iterable[str], *, offered: Iterable[str], picked: Iterable[str]
) -> list[str]:
    """Update the stored exclusions with what this form offered and the user picked."""
    picked_set = set(picked)
    excluded = {item for item in offered if item not in picked_set}
    return sorted(excluded | (set(stored) - set(offered)))


class CompanionConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Bitfocus Companion."""

    VERSION = 1
    MINOR_VERSION = 1

    def __init__(self) -> None:
        """Initialise the flow state."""
        self._data: dict[str, Any] = {}
        self._result: ValidationResult | None = None
        self._probed = False
        self._probe_failure: ValidationFailure | None = None
        self._discovered_name = ""
        self._discovered_version = ""

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: Any) -> CompanionOptionsFlow:
        """Return the options flow."""
        return CompanionOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle a flow started by the user."""
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {"flag": REST_API_FLAG, "docs": DOCS_URL}

        if user_input is not None:
            user_input[CONF_PORT] = int(user_input[CONF_PORT])
            _normalise_host(user_input)
            self._async_abort_entries_match(
                {CONF_HOST: user_input[CONF_HOST], CONF_PORT: user_input[CONF_PORT]}
            )
            await self.async_set_unique_id(
                f"{user_input[CONF_HOST]}:{user_input[CONF_PORT]}".lower(),
                raise_on_progress=False,
            )
            self._abort_if_unique_id_configured()

            outcome = await async_validate_input(self.hass, user_input)
            if isinstance(outcome, ValidationResult):
                self._data = dict(user_input)
                self._result = outcome
                return await self.async_step_devices()
            errors = outcome.errors
            placeholders |= outcome.placeholders

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_SCHEMA, user_input or {}
            ),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_zeroconf(
        self, discovery_info: ZeroconfServiceInfo
    ) -> ConfigFlowResult:
        """Handle a Companion instance found over mDNS."""
        host = str(discovery_info.ip_address)
        properties = discovery_info.properties
        machine_id = str(properties.get("id") or "")

        # Companion announces a stable machine id, so an instance that moved to
        # another address is the same instance, not a second one.
        if machine_id:
            for entry in self._async_current_entries():
                if entry.data.get(CONF_MACHINE_ID) != machine_id:
                    continue
                moved_to = f"{host}:{entry.data[CONF_PORT]}".lower()
                if any(
                    other.unique_id == moved_to and other.entry_id != entry.entry_id
                    for other in self._async_current_entries()
                ):
                    # A stale entry still claims the address. Renaming onto it would
                    # leave two entries sharing a unique id.
                    return self.async_abort(reason="already_configured")
                return self.async_update_reload_and_abort(
                    entry,
                    unique_id=moved_to,
                    data_updates={CONF_HOST: host},
                    title=f"{host}:{entry.data[CONF_PORT]}",
                    reason="already_configured",
                    # Zeroconf re-announces on every start. Only a move is news.
                    reload_even_if_entry_is_unchanged=False,
                )

        self._async_abort_entries_match({CONF_HOST: host, CONF_PORT: DEFAULT_PORT})
        await self.async_set_unique_id(f"{host}:{DEFAULT_PORT}".lower())
        self._abort_if_unique_id_configured(updates={CONF_HOST: host})

        name = discovery_info.name.removesuffix(f".{discovery_info.type}").removesuffix(
            "."
        )
        self._data = {
            CONF_HOST: host,
            CONF_PORT: DEFAULT_PORT,
            CONF_SSL: False,
            CONF_VERIFY_SSL: True,
            CONF_MACHINE_ID: machine_id,
        }
        self.context["title_placeholders"] = {"name": name}
        self._discovered_name = name
        self._discovered_version = str(properties.get("version", "")) or "unknown"
        return await self.async_step_discovery_confirm()

    async def async_step_discovery_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the port and token of a discovered instance.

        Only asks when the defaults do not work. Companion's tokens are fixed strings
        today, so trying the one that reaches everything costs one request and saves
        the user from typing a magic word.
        """
        errors: dict[str, str] = {}
        placeholders = {
            "name": self._discovered_name,
            "version": self._discovered_version,
            "flag": REST_API_FLAG,
            "docs": DOCS_URL,
        }

        # Opening the discovery card re-runs this step, so probe only the first time.
        if user_input is None and not self._probed:
            self._probed = True
            candidate = {**self._data, CONF_TOKEN: DEFAULT_TOKEN}
            outcome = await async_validate_input(self.hass, candidate)
            if isinstance(outcome, ValidationResult):
                self._data = candidate
                self._result = outcome
                return await self.async_step_devices()
            # Say why the default token did not work, instead of asking for a port
            # and a token when the real problem is the flag or the version.
            self._probe_failure = outcome

        if user_input is None and self._probe_failure is not None:
            errors = self._probe_failure.errors
            placeholders |= self._probe_failure.placeholders

        if user_input is not None:
            candidate = {**self._data, **user_input}
            candidate[CONF_PORT] = int(candidate[CONF_PORT])
            outcome = await async_validate_input(self.hass, candidate)
            if isinstance(outcome, ValidationResult):
                self._data = candidate
                self._result = outcome
                await self.async_set_unique_id(
                    f"{candidate[CONF_HOST]}:{candidate[CONF_PORT]}".lower(),
                    raise_on_progress=False,
                )
                self._abort_if_unique_id_configured()
                return await self.async_step_devices()
            errors = outcome.errors
            placeholders |= outcome.placeholders

        return self.async_show_form(
            step_id="discovery_confirm",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Required(CONF_PORT, default=DEFAULT_PORT): NumberSelector(
                            NumberSelectorConfig(
                                min=1, max=65535, mode=NumberSelectorMode.BOX, step=1
                            )
                        ),
                        vol.Required(CONF_TOKEN, default=DEFAULT_TOKEN): TextSelector(
                            TextSelectorConfig(type=TextSelectorType.PASSWORD)
                        ),
                    }
                ),
                user_input or {},
            ),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_devices(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user pick which surfaces and connections to add."""
        if self._result is None:  # pragma: no cover - defensive
            return await self.async_step_user()
        surfaces = self._result.surfaces
        connections = self._result.connections

        if not surfaces and not connections:
            # Nothing to pick, but a discovered instance still needs a yes.
            if self.source == SOURCE_ZEROCONF:
                return await self.async_step_confirm()
            return self._async_create_entry({})

        if user_input is not None:
            return self._async_create_entry(
                _invert_selection(user_input, surfaces, connections, {})
            )

        return self.async_show_form(
            step_id="devices",
            data_schema=_device_schema(
                surfaces,
                connections,
                excluded_surfaces=set(),
                excluded_connections=set(),
            ),
            description_placeholders={
                "surface_count": str(len(surfaces)),
                "connection_count": str(len(connections)),
            },
        )

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm a discovered instance that has nothing to pick yet."""
        if user_input is not None:
            return self._async_create_entry({})
        return self.async_show_form(
            step_id="confirm",
            description_placeholders={"name": self._discovered_name},
        )

    @callback
    def _async_create_entry(self, options: dict[str, Any]) -> ConfigFlowResult:
        """Store the entry once everything checked out."""
        return self.async_create_entry(
            title=f"{self._data[CONF_HOST]}:{self._data[CONF_PORT]}",
            data=self._data,
            options=options,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle a token that stopped working."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new token."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        placeholders = {"host": entry.title, "flag": REST_API_FLAG, "docs": DOCS_URL}

        if user_input is not None:
            outcome = await async_validate_input(
                self.hass, {**entry.data, **user_input}
            )
            if isinstance(outcome, ValidationResult):
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_TOKEN: user_input[CONF_TOKEN]}
                )
            errors = outcome.errors
            placeholders |= outcome.placeholders

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_TOKEN_SCHEMA,
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change host, port, TLS settings or token of an existing entry."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {"flag": REST_API_FLAG, "docs": DOCS_URL}

        if user_input is not None:
            user_input[CONF_PORT] = int(user_input[CONF_PORT])
            _normalise_host(user_input)
            # Moving Companion to another address is a reconfigure, not a new entry.
            # The unique id moves with it, unless another entry is already there.
            unique_id = f"{user_input[CONF_HOST]}:{user_input[CONF_PORT]}".lower()
            if any(
                other.entry_id != entry.entry_id and other.unique_id == unique_id
                for other in self._async_current_entries()
            ):
                return self.async_abort(reason="already_configured")

            outcome = await async_validate_input(self.hass, user_input)
            if isinstance(outcome, ValidationResult):
                return self.async_update_reload_and_abort(
                    entry,
                    unique_id=unique_id,
                    data_updates=user_input,
                    title=f"{user_input[CONF_HOST]}:{user_input[CONF_PORT]}",
                )
            errors = outcome.errors
            placeholders |= outcome.placeholders

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_SCHEMA, user_input or entry.data
            ),
            errors=errors,
            description_placeholders=placeholders,
        )


class CompanionOptionsFlow(OptionsFlowWithReload):
    """Change which devices are exposed and how often they are polled."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the options menu."""
        return self.async_show_menu(step_id="init", menu_options=["devices", "polling"])

    async def async_step_devices(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Re-pick the surfaces and connections to expose."""
        outcome = await async_validate_input(self.hass, self.config_entry.data)
        if isinstance(outcome, ValidationFailure):
            return self.async_abort(
                reason=next(iter(outcome.errors.values())),
                description_placeholders={
                    "flag": REST_API_FLAG,
                    "docs": DOCS_URL,
                    **outcome.placeholders,
                },
            )

        if user_input is not None:
            return self.async_create_entry(
                data={
                    **self.config_entry.options,
                    **_invert_selection(
                        user_input,
                        outcome.surfaces,
                        outcome.connections,
                        self.config_entry.options,
                    ),
                }
            )

        excluded_surfaces = set(
            self.config_entry.options.get(CONF_EXCLUDED_SURFACES, [])
        )
        excluded_connections = set(
            self.config_entry.options.get(CONF_EXCLUDED_CONNECTIONS, [])
        )
        return self.async_show_form(
            step_id="devices",
            data_schema=_device_schema(
                outcome.surfaces,
                outcome.connections,
                excluded_surfaces=excluded_surfaces,
                excluded_connections=excluded_connections,
            ),
            description_placeholders={
                "surface_count": str(len(outcome.surfaces)),
                "connection_count": str(len(outcome.connections)),
            },
        )

    async def async_step_polling(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change how often Companion is polled."""
        if user_input is not None:
            return self.async_create_entry(
                data={
                    **self.config_entry.options,
                    CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
                }
            )

        current = self.config_entry.options.get(
            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
        )
        return self.async_show_form(
            step_id="polling",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SCAN_INTERVAL, default=current): NumberSelector(
                        NumberSelectorConfig(
                            min=MIN_SCAN_INTERVAL,
                            max=MAX_SCAN_INTERVAL,
                            step=1,
                            mode=NumberSelectorMode.BOX,
                            unit_of_measurement="s",
                        )
                    )
                }
            ),
        )
