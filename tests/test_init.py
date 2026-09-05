"""Tests for setting up and tearing down the Companion integration."""

from __future__ import annotations

from datetime import timedelta

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.bitfocus_companion import async_remove_config_entry_device
from custom_components.bitfocus_companion.const import (
    CONF_EXCLUDED_CONNECTIONS,
    DOMAIN,
    ISSUE_API_DISABLED,
    ISSUE_CONNECTIONS_SCOPE_LOST,
)
from custom_components.bitfocus_companion.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .conftest import (
    ADMIN_UI,
    BASE,
    CONNECTION,
    CONNECTIONS_URL,
    OPENAPI_DOC,
    OPENAPI_DOC_SURFACES_ONLY,
    OPENAPI_URL,
    SURFACE,
    SURFACE_TWO,
    SURFACES_URL,
    collection,
    poll,
    serve,
    setup_entry,
)


def _device_count(hass: HomeAssistant, entry: MockConfigEntry) -> int:
    """Return how many devices the config entry owns."""
    return len(dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id))


async def test_setup_and_unload(
    hass: HomeAssistant,
    mock_api: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A healthy instance loads a device per surface, connection and hub."""
    await setup_entry(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.LOADED

    devices = dr.async_entries_for_config_entry(
        dr.async_get(hass), mock_config_entry.entry_id
    )
    assert len(devices) == 4
    assert {device.model for device in devices} == {
        "Companion",
        "Emulator",
        "Elgato Stream Deck XL",
        "bmd-atem",
    }

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    assert mock_config_entry.state is ConfigEntryState.NOT_LOADED


async def test_setup_retries_when_api_is_off(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A Companion without the REST API is a retry, not a hard failure."""
    aioclient_mock.get(OPENAPI_URL, status=404, text="Not found")
    aioclient_mock.get(f"{BASE}/", status=200, text=ADMIN_UI)

    await setup_entry(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_setup_retries_when_unreachable(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """An unreachable Companion is a retry."""
    aioclient_mock.get(OPENAPI_URL, exc=TimeoutError())
    await setup_entry(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_setup_starts_reauth_on_bad_token(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A rejected token sends the user to the reauth flow."""
    aioclient_mock.get(OPENAPI_URL, json=OPENAPI_DOC)
    aioclient_mock.get(
        SURFACES_URL, status=401, json={"error": {"code": "UNAUTHORIZED"}}
    )
    aioclient_mock.get(
        CONNECTIONS_URL, status=401, json={"error": {"code": "UNAUTHORIZED"}}
    )

    await setup_entry(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR
    assert any(
        flow["context"]["source"] == "reauth"
        for flow in hass.config_entries.flow.async_progress()
    )


async def test_api_disabled_issue_appears_and_clears(
    hass: HomeAssistant,
    mock_api: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Losing the REST API while running is reported as a repair issue."""
    await setup_entry(hass, mock_config_entry)
    issue_id = f"{ISSUE_API_DISABLED}_{mock_config_entry.entry_id}"
    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, issue_id) is None

    mock_api.clear_requests()
    mock_api.get(OPENAPI_URL, status=404, text="Not found")
    mock_api.get(SURFACES_URL, status=404, text="Not found")
    mock_api.get(CONNECTIONS_URL, status=404, text="Not found")
    mock_api.get(f"{BASE}/", status=200, text=ADMIN_UI)

    freezer.tick(timedelta(seconds=31))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert registry.async_get_issue(DOMAIN, issue_id) is not None
    assert hass.states.get("number.test_surface_brightness").state == "unavailable"

    serve(mock_api, [SURFACE, SURFACE_TWO], [CONNECTION])

    freezer.tick(timedelta(seconds=31))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert registry.async_get_issue(DOMAIN, issue_id) is None


async def test_stale_devices_are_dropped(
    hass: HomeAssistant,
    mock_api: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A surface removed in Companion loses its device, but not on the first poll."""
    await setup_entry(hass, mock_config_entry)
    assert _device_count(hass, mock_config_entry) == 4

    serve(mock_api, [SURFACE], [CONNECTION])

    await poll(hass, freezer)
    assert _device_count(hass, mock_config_entry) == 4

    await poll(hass, freezer)
    assert _device_count(hass, mock_config_entry) == 3


async def test_a_single_short_poll_keeps_the_devices(
    hass: HomeAssistant,
    mock_api: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Companion answering with nothing while it starts up deletes nothing."""
    await setup_entry(hass, mock_config_entry)

    serve(mock_api, [], [])
    await poll(hass, freezer)
    assert _device_count(hass, mock_config_entry) == 4

    serve(mock_api, [SURFACE, SURFACE_TWO], [CONNECTION])
    await poll(hass, freezer)
    assert _device_count(hass, mock_config_entry) == 4
    assert hass.states.get("number.test_surface_brightness").state == "100"


async def test_renames_in_companion_follow_through(
    hass: HomeAssistant,
    mock_api: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Renaming a surface or connection in Companion renames the device here."""
    await setup_entry(hass, mock_config_entry)
    registry = dr.async_get(hass)
    surface_key = (DOMAIN, f"{mock_config_entry.entry_id}_surface_{SURFACE['id']}")
    connection_key = (
        DOMAIN,
        f"{mock_config_entry.entry_id}_connection_{CONNECTION['id']}",
    )
    assert registry.async_get_device(identifiers={surface_key}).name == "Test surface"

    serve(
        mock_api,
        [{**SURFACE, "name": "Front of house"}],
        [{**CONNECTION, "label": "ATEM 2", "moduleVersionId": "1.3.0"}],
    )
    await poll(hass, freezer)

    assert registry.async_get_device(identifiers={surface_key}).name == "Front of house"
    connection_device = registry.async_get_device(identifiers={connection_key})
    assert connection_device.name == "ATEM 2"
    assert connection_device.sw_version == "1.3.0"


async def test_diagnostics_redacts_the_token(
    hass: HomeAssistant,
    mock_api: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Diagnostics carry the state but never the token."""
    await setup_entry(hass, mock_config_entry)
    diagnostics = await async_get_config_entry_diagnostics(hass, mock_config_entry)
    assert diagnostics["entry"]["data"]["token"] == "**REDACTED**"
    assert diagnostics["companion_version"] == "5.1.0"
    assert len(diagnostics["surfaces"]) == 2
    assert len(diagnostics["connections"]) == 1


async def test_setup_with_a_surfaces_only_token(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Only surfaces are exposed when the token cannot read connections."""
    aioclient_mock.get(OPENAPI_URL, json=OPENAPI_DOC)
    aioclient_mock.get(SURFACES_URL, json=collection([SURFACE]))
    aioclient_mock.get(
        CONNECTIONS_URL,
        status=403,
        json={"error": {"code": "FORBIDDEN", "message": "Insufficient scope"}},
    )

    await setup_entry(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert hass.states.get("number.test_surface_brightness") is not None
    assert hass.states.get("switch.atem_enabled") is None
    # Setting up with a narrow token on purpose is not a problem to repair.
    assert (
        ir.async_get(hass).async_get_issue(
            DOMAIN, f"{ISSUE_CONNECTIONS_SCOPE_LOST}_{mock_config_entry.entry_id}"
        )
        is None
    )


async def test_losing_the_connections_scope_keeps_the_devices(
    hass: HomeAssistant,
    mock_api: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A token that stops reaching connections raises an issue, it does not delete."""
    await setup_entry(hass, mock_config_entry)
    assert _device_count(hass, mock_config_entry) == 4

    mock_api.clear_requests()
    mock_api.get(OPENAPI_URL, json=OPENAPI_DOC)
    mock_api.get(SURFACES_URL, json=collection([SURFACE, SURFACE_TWO]))
    mock_api.get(
        CONNECTIONS_URL,
        status=403,
        json={"error": {"code": "FORBIDDEN", "message": "Insufficient scope"}},
    )

    await poll(hass, freezer)
    await poll(hass, freezer)

    assert _device_count(hass, mock_config_entry) == 4
    assert hass.states.get("switch.atem_enabled").state == STATE_UNAVAILABLE
    assert ir.async_get(hass).async_get_issue(
        DOMAIN, f"{ISSUE_CONNECTIONS_SCOPE_LOST}_{mock_config_entry.entry_id}"
    )

    serve(mock_api, [SURFACE, SURFACE_TWO], [CONNECTION])
    await poll(hass, freezer)

    assert hass.states.get("switch.atem_enabled").state == STATE_ON
    assert (
        ir.async_get(hass).async_get_issue(
            DOMAIN, f"{ISSUE_CONNECTIONS_SCOPE_LOST}_{mock_config_entry.entry_id}"
        )
        is None
    )


async def test_the_scope_issue_survives_a_reload(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Reloading with a narrowed token still reports the connections left behind."""
    serve(aioclient_mock, [SURFACE, SURFACE_TWO], [CONNECTION])
    await setup_entry(hass, mock_config_entry)
    assert _device_count(hass, mock_config_entry) == 4

    aioclient_mock.clear_requests()
    aioclient_mock.get(OPENAPI_URL, json=OPENAPI_DOC)
    aioclient_mock.get(SURFACES_URL, json=collection([SURFACE, SURFACE_TWO]))
    aioclient_mock.get(
        CONNECTIONS_URL,
        status=403,
        json={"error": {"code": "FORBIDDEN", "message": "Insufficient scope"}},
    )
    await hass.config_entries.async_reload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert _device_count(hass, mock_config_entry) == 4
    assert ir.async_get(hass).async_get_issue(
        DOMAIN, f"{ISSUE_CONNECTIONS_SCOPE_LOST}_{mock_config_entry.entry_id}"
    )


async def test_a_companion_that_moved_the_surfaces_resource(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A future Companion without our resource version fails with its own message."""
    aioclient_mock.get(
        OPENAPI_URL,
        json={
            "info": {"title": "Bitfocus Companion REST API", "version": "7.0.0"},
            "paths": {"/surfaces/v2": {"get": {}}},
        },
    )
    await setup_entry(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR
    assert "surfaces/v1" in str(mock_config_entry.reason)


async def test_a_companion_without_the_connections_resource(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Surfaces keep working, and no request is wasted on the missing resource."""
    aioclient_mock.get(OPENAPI_URL, json=OPENAPI_DOC_SURFACES_ONLY)
    aioclient_mock.get(SURFACES_URL, json=collection([SURFACE]))
    await setup_entry(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert hass.states.get("number.test_surface_brightness") is not None
    assert hass.states.get("switch.atem_enabled") is None
    assert not any(
        str(call[1]).endswith("/connections/v1") for call in aioclient_mock.mock_calls
    )


async def test_an_empty_first_poll_keeps_the_devices(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Setting up against a Companion that is still starting deletes nothing.

    Home Assistant and Companion often restart together, and the first poll can land
    while Companion is still enumerating its surfaces.
    """
    serve(aioclient_mock, [SURFACE, SURFACE_TWO], [CONNECTION])
    await setup_entry(hass, mock_config_entry)
    assert _device_count(hass, mock_config_entry) == 4

    await hass.config_entries.async_unload(mock_config_entry.entry_id)
    serve(aioclient_mock, [], [])
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert _device_count(hass, mock_config_entry) == 4


async def test_a_surface_that_comes_back_gets_its_entities_back(
    hass: HomeAssistant,
    mock_api: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A surface removed and re-added in Companion works again without a reload."""
    await setup_entry(hass, mock_config_entry)
    assert hass.states.get("number.front_of_house_brightness") is not None

    serve(mock_api, [SURFACE], [CONNECTION])
    await poll(hass, freezer)
    await poll(hass, freezer)
    assert hass.states.get("number.front_of_house_brightness") is None

    serve(mock_api, [SURFACE, SURFACE_TWO], [CONNECTION])
    await poll(hass, freezer)

    assert hass.states.get("number.front_of_house_brightness").state == "80"


async def test_removing_a_device_from_an_unloaded_entry(
    hass: HomeAssistant,
    mock_api: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """The delete button on a device works while the entry is not loaded."""
    await setup_entry(hass, mock_config_entry)
    device = dr.async_get(hass).async_get_device(
        identifiers={(DOMAIN, f"{mock_config_entry.entry_id}_surface_{SURFACE['id']}")}
    )
    assert device is not None
    await hass.config_entries.async_unload(mock_config_entry.entry_id)

    assert await async_remove_config_entry_device(hass, mock_config_entry, device)


async def test_only_the_connections_resource_disappearing(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Losing connections mid-run keeps the surfaces up and blames the right thing."""
    serve(aioclient_mock, [SURFACE, SURFACE_TWO], [CONNECTION])
    await setup_entry(hass, mock_config_entry)

    aioclient_mock.clear_requests()
    aioclient_mock.get(OPENAPI_URL, json=OPENAPI_DOC)
    aioclient_mock.get(SURFACES_URL, json=collection([SURFACE, SURFACE_TWO]))
    aioclient_mock.get(CONNECTIONS_URL, status=404, text="Not found")
    aioclient_mock.get(f"{BASE}/", status=200, text=ADMIN_UI)
    await poll(hass, freezer)

    assert hass.states.get("number.test_surface_brightness").state == "100"
    assert hass.states.get("switch.atem_enabled").state == STATE_UNAVAILABLE
    registry = ir.async_get(hass)
    assert (
        registry.async_get_issue(
            DOMAIN, f"{ISSUE_API_DISABLED}_{mock_config_entry.entry_id}"
        )
        is None
    )
    assert registry.async_get_issue(
        DOMAIN, f"{ISSUE_CONNECTIONS_SCOPE_LOST}_{mock_config_entry.entry_id}"
    )


async def test_repair_issues_go_with_the_entry(
    hass: HomeAssistant,
    mock_api: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Deleting the entry takes its repair notices with it."""
    await setup_entry(hass, mock_config_entry)
    mock_api.clear_requests()
    mock_api.get(OPENAPI_URL, status=404, text="Not found")
    mock_api.get(SURFACES_URL, status=404, text="Not found")
    mock_api.get(f"{BASE}/", status=200, text=ADMIN_UI)
    await poll(hass, freezer)
    assert ir.async_get(hass).async_get_issue(
        DOMAIN, f"{ISSUE_API_DISABLED}_{mock_config_entry.entry_id}"
    )

    await hass.config_entries.async_remove(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert (
        ir.async_get(hass).async_get_issue(
            DOMAIN, f"{ISSUE_API_DISABLED}_{mock_config_entry.entry_id}"
        )
        is None
    )


async def test_a_returning_surface_does_not_clash_with_itself(
    hass: HomeAssistant,
    mock_api: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A surface missing for one poll keeps its device, so it must not be re-added.

    Adding a second entity for a unique id that is still registered makes Home
    Assistant log an error and throw the new entity away.
    """
    await setup_entry(hass, mock_config_entry)

    serve(mock_api, [SURFACE], [CONNECTION])
    await poll(hass, freezer)
    serve(mock_api, [SURFACE, SURFACE_TWO], [CONNECTION])
    await poll(hass, freezer)

    assert "does not generate unique IDs" not in caplog.text
    assert hass.states.get("number.front_of_house_brightness").state == "80"


async def test_unticking_a_connection_removes_it_even_while_denied(
    hass: HomeAssistant,
    mock_api: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Unticking is a decision, so it does not wait for the token to recover."""
    await setup_entry(hass, mock_config_entry)
    assert _device_count(hass, mock_config_entry) == 4

    mock_api.clear_requests()
    mock_api.get(OPENAPI_URL, json=OPENAPI_DOC)
    mock_api.get(SURFACES_URL, json=collection([SURFACE, SURFACE_TWO]))
    mock_api.get(
        CONNECTIONS_URL,
        status=403,
        json={"error": {"code": "FORBIDDEN", "message": "Insufficient scope"}},
    )
    hass.config_entries.async_update_entry(
        mock_config_entry, options={CONF_EXCLUDED_CONNECTIONS: [CONNECTION["id"]]}
    )
    await hass.async_block_till_done()
    await poll(hass, freezer)

    assert _device_count(hass, mock_config_entry) == 3
