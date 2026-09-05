"""Tests for the Bitfocus Companion config flow."""

from __future__ import annotations

import json
import pathlib
import re
from unittest.mock import AsyncMock

import pytest
from homeassistant.config_entries import SOURCE_USER, SOURCE_ZEROCONF
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_SCAN_INTERVAL, CONF_TOKEN
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.bitfocus_companion.const import (
    CONF_EXCLUDED_CONNECTIONS,
    CONF_EXCLUDED_SURFACES,
    DEFAULT_TOKEN,
    DOMAIN,
)

from .conftest import (
    ADMIN_UI,
    BASE,
    CONNECTION,
    CONNECTIONS_URL,
    ENTRY_DATA,
    HOST,
    OPENAPI_DOC,
    OPENAPI_DOC_SURFACES_ONLY,
    OPENAPI_URL,
    PORT,
    SURFACE,
    SURFACE_TWO,
    SURFACES_URL,
    collection,
    serve,
)

ZEROCONF_INFO = ZeroconfServiceInfo(
    ip_address="192.0.2.10",
    ip_addresses=["192.0.2.10"],
    hostname="companion.local.",
    name="Companion (studio)._companion-satellite-tcp._tcp.local.",
    port=16622,
    type="_companion-satellite-tcp._tcp.local.",
    properties={"id": "abc123", "version": "5.1.0", "protocolVersion": "2"},
)


async def test_user_flow(
    hass: HomeAssistant, mock_api: AiohttpClientMocker, mock_setup_entry: AsyncMock
) -> None:
    """A complete manual setup keeps every surface and connection."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], ENTRY_DATA
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "devices"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "surfaces": [SURFACE["id"], SURFACE_TWO["id"]],
            "connections": [CONNECTION["id"]],
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == f"{HOST}:{PORT}"
    assert result["data"] == ENTRY_DATA
    assert result["options"] == {
        CONF_EXCLUDED_SURFACES: [],
        CONF_EXCLUDED_CONNECTIONS: [],
    }
    assert result["result"].unique_id == f"{HOST}:{PORT}"


async def test_user_flow_deselecting_a_surface(
    hass: HomeAssistant, mock_api: AiohttpClientMocker, mock_setup_entry: AsyncMock
) -> None:
    """Unticked devices are stored as exclusions."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], ENTRY_DATA
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"surfaces": [SURFACE["id"]], "connections": []}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"] == {
        CONF_EXCLUDED_SURFACES: [SURFACE_TWO["id"]],
        CONF_EXCLUDED_CONNECTIONS: [CONNECTION["id"]],
    }


async def test_user_flow_without_devices(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_setup_entry: AsyncMock,
) -> None:
    """An empty Companion skips the picker."""
    serve(aioclient_mock)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], ENTRY_DATA
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"] == {}


def _placeholders_cover(result: dict, section: str = "config") -> bool:
    """Return whether the step supplies every placeholder its strings ask for."""
    strings = json.loads(
        (
            pathlib.Path(__file__).parent.parent
            / "custom_components/bitfocus_companion/translations/en.json"
        ).read_text()
    )[section]
    texts = [strings["error"][key] for key in (result.get("errors") or {}).values()]
    if result["type"] is FlowResultType.ABORT:
        texts.append(strings["abort"][result["reason"]])
    supplied = set(result.get("description_placeholders") or {})
    return all(set(re.findall(r"{(\w+)}", text)) <= supplied for text in texts)


def _register_failure(mock: AiohttpClientMocker, mode: str) -> None:
    """Register the responses that produce one particular failure."""
    if mode == "cannot_connect":
        mock.get(OPENAPI_URL, exc=TimeoutError())
    elif mode == "not_companion":
        mock.get(OPENAPI_URL, status=404, text="nope")
        mock.get(f"{BASE}/", status=200, text="<html><title>nginx</title></html>")
    elif mode == "api_unavailable":
        mock.get(OPENAPI_URL, status=404, text="Not found")
        mock.get(f"{BASE}/", status=200, text=ADMIN_UI)
    elif mode == "invalid_auth":
        mock.get(OPENAPI_URL, json=OPENAPI_DOC)
        mock.get(SURFACES_URL, status=401, json={"error": {"code": "UNAUTHORIZED"}})
    elif mode == "insufficient_scope":
        mock.get(OPENAPI_URL, json=OPENAPI_DOC)
        mock.get(SURFACES_URL, status=403, json={"error": {"code": "FORBIDDEN"}})
    elif mode == "unknown":
        mock.get(OPENAPI_URL, json=OPENAPI_DOC)
        mock.get(SURFACES_URL, status=500, text="boom")


@pytest.mark.parametrize(
    ("mode", "field"),
    [
        ("cannot_connect", CONF_HOST),
        ("not_companion", CONF_HOST),
        ("api_unavailable", "base"),
        ("invalid_auth", CONF_TOKEN),
        ("insufficient_scope", CONF_TOKEN),
        ("unknown", "base"),
    ],
)
async def test_user_flow_errors_and_recovery(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_setup_entry: AsyncMock,
    mode: str,
    field: str,
) -> None:
    """Every failure mode gets its own message, and the flow recovers after it."""
    _register_failure(aioclient_mock, mode)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], ENTRY_DATA
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {field: mode}
    # Every message that tells the user to go and change Companion carries the link,
    # otherwise the dialog shows a literal {docs}.
    assert _placeholders_cover(result)

    serve(aioclient_mock, [SURFACE], [])

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], ENTRY_DATA
    )
    assert result["step_id"] == "devices"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"surfaces": [SURFACE["id"]]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_already_configured(
    hass: HomeAssistant,
    mock_api: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """The same host and port cannot be added twice."""
    mock_config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], ENTRY_DATA
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_zeroconf_flow(
    hass: HomeAssistant, mock_api: AiohttpClientMocker, mock_setup_entry: AsyncMock
) -> None:
    """A discovered instance that takes the default token asks nothing extra."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_ZEROCONF}, data=ZEROCONF_INFO
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "devices"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"surfaces": [SURFACE["id"]], "connections": [CONNECTION["id"]]},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_HOST] == HOST
    assert result["data"][CONF_TOKEN] == DEFAULT_TOKEN


async def test_zeroconf_flow_asks_when_the_default_token_fails(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_setup_entry: AsyncMock,
) -> None:
    """Only then does the port and token form appear."""
    aioclient_mock.get(OPENAPI_URL, json=OPENAPI_DOC)
    aioclient_mock.get(
        SURFACES_URL, status=401, json={"error": {"code": "UNAUTHORIZED"}}
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_ZEROCONF}, data=ZEROCONF_INFO
    )
    assert result["step_id"] == "discovery_confirm"
    assert result["description_placeholders"]["version"] == "5.1.0"

    serve(aioclient_mock, [SURFACE], [CONNECTION])
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PORT: PORT, CONF_TOKEN: "cpn_write"}
    )
    assert result["step_id"] == "devices"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"surfaces": [SURFACE["id"]]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_TOKEN] == "cpn_write"


async def test_zeroconf_already_configured(
    hass: HomeAssistant,
    mock_api: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A known instance is not offered again."""
    mock_config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_ZEROCONF}, data=ZEROCONF_INFO
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_flow(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A rejected token can be replaced, and a wrong one is reported."""
    mock_config_entry.add_to_hass(hass)
    result = await mock_config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    _register_failure(aioclient_mock, "invalid_auth")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_TOKEN: "wrong"}
    )
    assert result["errors"] == {CONF_TOKEN: "invalid_auth"}

    serve(aioclient_mock, [SURFACE], [CONNECTION])

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_TOKEN: "cpn_admin"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert mock_config_entry.data[CONF_TOKEN] == "cpn_admin"


async def test_reconfigure_flow_moves_the_instance(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Companion can be pointed at a new address without losing the entry."""
    mock_config_entry.add_to_hass(hass)
    new_host = "192.0.2.20"
    aioclient_mock.get(
        f"http://{new_host}:{PORT}/api/v2/openapi.json", json=OPENAPI_DOC
    )
    aioclient_mock.get(
        f"http://{new_host}:{PORT}/api/v2/surfaces/v1", json=collection([SURFACE])
    )
    aioclient_mock.get(
        f"http://{new_host}:{PORT}/api/v2/connections/v1", json=collection([CONNECTION])
    )

    result = await mock_config_entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**ENTRY_DATA, CONF_HOST: new_host}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert mock_config_entry.data[CONF_HOST] == new_host
    assert mock_config_entry.unique_id == f"{new_host}:{PORT}"


async def test_reconfigure_flow_rejects_a_taken_address(
    hass: HomeAssistant,
    mock_api: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Reconfiguring onto another entry's address is refused."""
    other = MockConfigEntry(
        domain=DOMAIN,
        data={**ENTRY_DATA, CONF_HOST: "192.0.2.30"},
        unique_id=f"192.0.2.30:{PORT}",
    )
    other.add_to_hass(hass)
    mock_config_entry.add_to_hass(hass)

    result = await mock_config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**ENTRY_DATA, CONF_HOST: "192.0.2.30"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_options_flow(
    hass: HomeAssistant,
    mock_api: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Devices and the polling interval can be changed after setup."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    assert result["type"] is FlowResultType.MENU

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "devices"}
    )
    assert result["step_id"] == "devices"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"surfaces": [SURFACE["id"]], "connections": []}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert mock_config_entry.options[CONF_EXCLUDED_SURFACES] == [SURFACE_TWO["id"]]

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "polling"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL: 60}
    )
    assert mock_config_entry.options[CONF_SCAN_INTERVAL] == 60


async def test_options_flow_aborts_when_companion_is_gone(
    hass: HomeAssistant,
    mock_api: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """The device picker says why it cannot show a list."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    mock_api.clear_requests()
    _register_failure(mock_api, "api_unavailable")

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "devices"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "api_unavailable"
    assert _placeholders_cover(result, "options")


async def test_user_flow_with_a_surfaces_only_token(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_setup_entry: AsyncMock,
) -> None:
    """A token without the connections scope still gets a working entry."""
    aioclient_mock.get(OPENAPI_URL, json=OPENAPI_DOC)
    aioclient_mock.get(SURFACES_URL, json=collection([SURFACE]))
    aioclient_mock.get(
        CONNECTIONS_URL,
        status=403,
        json={"error": {"code": "FORBIDDEN", "message": "Insufficient scope"}},
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], ENTRY_DATA
    )
    assert result["step_id"] == "devices"
    assert result["description_placeholders"]["connection_count"] == "0"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"surfaces": [SURFACE["id"]]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_on_a_companion_without_our_resource_version(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_setup_entry: AsyncMock,
) -> None:
    """A Companion serving other resource versions gets its own message."""
    aioclient_mock.get(
        OPENAPI_URL,
        json={
            "info": {"title": "Bitfocus Companion REST API", "version": "7.0.0"},
            "paths": {"/surfaces/v2": {"get": {}}},
        },
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], ENTRY_DATA
    )
    assert result["errors"] == {"base": "unsupported_api"}
    assert result["description_placeholders"]["resource"].endswith("/surfaces/v1")


async def test_user_flow_without_the_connections_resource(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_setup_entry: AsyncMock,
) -> None:
    """The picker explains an empty connection list from a missing resource too."""
    aioclient_mock.get(OPENAPI_URL, json=OPENAPI_DOC_SURFACES_ONLY)
    aioclient_mock.get(SURFACES_URL, json=collection([SURFACE]))

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], ENTRY_DATA
    )
    assert result["step_id"] == "devices"
    assert result["description_placeholders"]["connection_count"] == "0"


async def test_options_keep_exclusions_for_devices_not_reported_now(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A device Companion is not reporting keeps whatever the user decided about it."""
    serve(aioclient_mock, [SURFACE, SURFACE_TWO], [CONNECTION])
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        options={
            CONF_EXCLUDED_SURFACES: [SURFACE_TWO["id"]],
            CONF_EXCLUDED_CONNECTIONS: [CONNECTION["id"]],
        },
    )
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    # Companion now reports neither the excluded surface nor any connection.
    serve(aioclient_mock, [SURFACE], [])

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "devices"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"surfaces": [SURFACE["id"]]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY

    assert mock_config_entry.options[CONF_EXCLUDED_SURFACES] == [SURFACE_TWO["id"]]
    assert mock_config_entry.options[CONF_EXCLUDED_CONNECTIONS] == [CONNECTION["id"]]


async def test_zeroconf_follows_an_instance_to_a_new_address(
    hass: HomeAssistant,
    mock_api: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A Companion that changed IP updates its entry instead of making a second one."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, data={**ENTRY_DATA, "machine_id": "abc123"}
    )

    moved = ZeroconfServiceInfo(
        ip_address="192.0.2.99",
        ip_addresses=["192.0.2.99"],
        hostname="companion.local.",
        name="Companion (studio)._companion-satellite-tcp._tcp.local.",
        port=16622,
        type="_companion-satellite-tcp._tcp.local.",
        properties={"id": "abc123", "version": "5.1.0"},
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_ZEROCONF}, data=moved
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert mock_config_entry.data[CONF_HOST] == "192.0.2.99"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


async def test_zeroconf_asks_before_adding_an_empty_companion(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_setup_entry: AsyncMock,
) -> None:
    """A discovery with nothing to pick still needs a yes from the user."""
    serve(aioclient_mock)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_ZEROCONF}, data=ZEROCONF_INFO
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "confirm"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_HOST] == HOST


async def test_opening_the_discovery_card_does_not_probe_again(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_setup_entry: AsyncMock,
) -> None:
    """Re-rendering the form must not send the request the first render sent."""
    aioclient_mock.get(OPENAPI_URL, json=OPENAPI_DOC)
    aioclient_mock.get(
        SURFACES_URL, status=401, json={"error": {"code": "UNAUTHORIZED"}}
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_ZEROCONF}, data=ZEROCONF_INFO
    )
    assert result["step_id"] == "discovery_confirm"
    # The probe said why the default token failed, so the form does not ask blind.
    assert result["errors"] == {CONF_TOKEN: "invalid_auth"}

    before = len(aioclient_mock.mock_calls)
    result = await hass.config_entries.flow.async_configure(result["flow_id"])

    assert result["step_id"] == "discovery_confirm"
    assert result["errors"] == {CONF_TOKEN: "invalid_auth"}
    assert len(aioclient_mock.mock_calls) == before


async def test_a_moved_instance_never_takes_another_entry_unique_id(
    hass: HomeAssistant,
    mock_api: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A stale entry on the new address blocks the rename instead of colliding."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={**ENTRY_DATA, "machine_id": "abc123"},
        unique_id=f"{HOST}:{PORT}",
    )
    stale = MockConfigEntry(
        domain=DOMAIN,
        data={**ENTRY_DATA, CONF_HOST: "192.0.2.99"},
        unique_id=f"192.0.2.99:{PORT}",
    )
    stale.add_to_hass(hass)

    moved = ZeroconfServiceInfo(
        ip_address="192.0.2.99",
        ip_addresses=["192.0.2.99"],
        hostname="companion.local.",
        name="Companion (studio)._companion-satellite-tcp._tcp.local.",
        port=16622,
        type="_companion-satellite-tcp._tcp.local.",
        properties={"id": "abc123", "version": "5.1.0"},
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_ZEROCONF}, data=moved
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert mock_config_entry.data[CONF_HOST] == HOST
    assert mock_config_entry.unique_id == f"{HOST}:{PORT}"
