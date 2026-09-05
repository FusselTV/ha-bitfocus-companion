"""Tests for the defensive corners: gone devices, failed writes, broken polls."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
from homeassistant.components.number import (
    ATTR_VALUE,
    SERVICE_SET_VALUE,
)
from homeassistant.components.number import (
    DOMAIN as NUMBER_DOMAIN,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import (
    ATTR_ENTITY_ID,
    EVENT_STATE_CHANGED,
    EVENT_STATE_REPORTED,
)
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from custom_components.bitfocus_companion import async_remove_config_entry_device
from custom_components.bitfocus_companion.api import MAX_IN_FLIGHT
from custom_components.bitfocus_companion.const import DOMAIN
from custom_components.bitfocus_companion.system_health import system_health_info

from .conftest import (
    ADMIN_UI,
    BASE,
    CONNECTION,
    CONNECTIONS_URL,
    OPENAPI_DOC,
    OPENAPI_URL,
    SURFACE,
    SURFACES_URL,
    collection,
    setup_entry,
)


def _entity(hass: HomeAssistant, domain: str, entity_id: str) -> object:
    """Return the live entity object behind an entity id."""
    return next(
        entity for entity in hass.data[domain].entities if entity.entity_id == entity_id
    )


async def test_entity_properties_when_the_data_is_gone(
    hass: HomeAssistant,
    mock_api: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Entities read as unknown between a device vanishing and being cleaned up."""
    await setup_entry(hass, mock_config_entry)
    coordinator = mock_config_entry.runtime_data

    brightness = _entity(hass, NUMBER_DOMAIN, "number.test_surface_brightness")
    page = _entity(hass, "sensor", "sensor.test_surface_page")
    connected = _entity(
        hass, "binary_sensor", "binary_sensor.test_surface_connectivity"
    )
    status = _entity(hass, "sensor", "sensor.atem_status")
    message = _entity(hass, "sensor", "sensor.atem_status_message")
    problem = _entity(hass, "binary_sensor", "binary_sensor.atem_problem")
    switch = _entity(hass, "switch", "switch.atem_enabled")
    screensaver = _entity(hass, "switch", "switch.test_surface_screensaver")
    policy = _entity(hass, "select", "select.atem_module_updates")

    coordinator.data.surfaces.clear()
    coordinator.data.connections.clear()

    assert brightness.native_value is None
    assert brightness.available is False
    assert page.native_value is None
    assert page.extra_state_attributes is None
    assert connected.is_on is None
    assert status.native_value is None
    assert message.native_value is None
    assert message.extra_state_attributes is None
    assert problem.is_on is None
    assert switch.is_on is None
    assert screensaver.is_on is None
    assert policy.current_option is None


async def test_surface_without_a_page(
    hass: HomeAssistant,
    mock_api: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A surface showing no page reports no page number."""
    await setup_entry(hass, mock_config_entry)
    coordinator = mock_config_entry.runtime_data
    surface = coordinator.data.surfaces[SURFACE["id"]]
    coordinator.data.surfaces[SURFACE["id"]] = replace(surface, page=None)
    page = _entity(hass, "sensor", "sensor.test_surface_page")
    assert page.native_value is None
    assert page.extra_state_attributes is None


async def test_connection_status_without_a_category(
    hass: HomeAssistant,
    mock_api: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A status with no category leaves the sensors unknown."""
    await setup_entry(hass, mock_config_entry)
    coordinator = mock_config_entry.runtime_data
    connection = coordinator.data.connections[CONNECTION["id"]]
    coordinator.data.connections[CONNECTION["id"]] = replace(
        connection,
        enabled=True,
        status=replace(connection.status, category=None, level=None, message=None),
    )

    assert _entity(hass, "sensor", "sensor.atem_status").native_value is None
    assert _entity(hass, "sensor", "sensor.atem_status_message").native_value is None
    assert _entity(hass, "binary_sensor", "binary_sensor.atem_problem").is_on is None


async def test_write_that_hits_a_switched_off_api(
    hass: HomeAssistant,
    mock_api: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A write after Companion lost its API is an error, not a silent no-op."""
    await setup_entry(hass, mock_config_entry)
    mock_api.patch(f"{SURFACES_URL}/{SURFACE['id']}", status=404, text="Not found")
    mock_api.get(f"{BASE}/", status=200, text=ADMIN_UI)

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            NUMBER_DOMAIN,
            SERVICE_SET_VALUE,
            {ATTR_ENTITY_ID: "number.test_surface_brightness", ATTR_VALUE: 10},
            blocking=True,
        )


async def test_write_with_a_dead_token_starts_reauth(
    hass: HomeAssistant,
    mock_api: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A token revoked while running sends the user to reauth."""
    await setup_entry(hass, mock_config_entry)
    mock_api.patch(
        f"{SURFACES_URL}/{SURFACE['id']}",
        status=401,
        json={"error": {"code": "UNAUTHORIZED"}},
    )

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            NUMBER_DOMAIN,
            SERVICE_SET_VALUE,
            {ATTR_ENTITY_ID: "number.test_surface_brightness", ATTR_VALUE: 10},
            blocking=True,
        )
    await hass.async_block_till_done()
    assert any(
        flow["context"]["source"] == "reauth"
        for flow in hass.config_entries.flow.async_progress()
    )


async def test_poll_failure_is_reported(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A broken response during setup keeps the entry retrying."""
    aioclient_mock.get(OPENAPI_URL, json=OPENAPI_DOC)
    aioclient_mock.get(SURFACES_URL, status=500, text="boom")

    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_removing_devices_by_hand(
    hass: HomeAssistant,
    mock_api: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Only devices Companion no longer reports may be deleted."""
    await setup_entry(hass, mock_config_entry)
    registry = dr.async_get(hass)

    live = registry.async_get_device(
        identifiers={(DOMAIN, f"{mock_config_entry.entry_id}_surface_{SURFACE['id']}")}
    )
    assert live is not None
    assert not await async_remove_config_entry_device(hass, mock_config_entry, live)

    stale = registry.async_get_or_create(
        config_entry_id=mock_config_entry.entry_id,
        identifiers={(DOMAIN, f"{mock_config_entry.entry_id}_surface_gone")},
        name="Gone",
    )
    assert await async_remove_config_entry_device(hass, mock_config_entry, stale)


async def test_system_health(
    hass: HomeAssistant,
    mock_api: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """System health reports the instance count, reachability and version."""
    await setup_entry(hass, mock_config_entry)
    info = await system_health_info(hass)
    assert info == {"instances": 1, "reachable": 1, "versions": "5.1.0"}


async def test_screensaver_is_unknown_without_a_brightness(
    hass: HomeAssistant,
    mock_api: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A surface Companion reports no brightness for has no screensaver state."""
    await setup_entry(hass, mock_config_entry)
    coordinator = mock_config_entry.runtime_data
    surface = coordinator.data.surfaces[SURFACE["id"]]
    coordinator.data.surfaces[SURFACE["id"]] = replace(surface, brightness=None)

    screensaver = _entity(hass, "switch", "switch.test_surface_screensaver")
    assert screensaver.is_on is None


async def test_two_surfaces_with_the_same_name(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Identical names give two devices and two distinct entities, not a collision."""
    twin = {**SURFACE, "id": "emulator:twin", "name": "Test surface"}
    aioclient_mock.get(OPENAPI_URL, json=OPENAPI_DOC)
    aioclient_mock.get(SURFACES_URL, json=collection([SURFACE, twin]))
    aioclient_mock.get(CONNECTIONS_URL, json=collection([]))
    await setup_entry(hass, mock_config_entry)

    first = hass.states.get("number.test_surface_brightness")
    second = hass.states.get("number.test_surface_brightness_2")
    assert first is not None
    assert second is not None
    assert first.attributes["friendly_name"] == second.attributes["friendly_name"]

    registry = dr.async_get(hass)
    for surface_id in (SURFACE["id"], "emulator:twin"):
        device = registry.async_get_device(
            identifiers={(DOMAIN, f"{mock_config_entry.entry_id}_surface_{surface_id}")}
        )
        assert device is not None
        assert device.serial_number == surface_id


async def test_a_write_only_refreshes_its_own_device(
    hass: HomeAssistant,
    mock_api: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Writing one surface must not re-render every entity of the config entry."""
    await setup_entry(hass, mock_config_entry)
    coordinator = mock_config_entry.runtime_data

    touched: set[str] = set()

    @callback
    def _record(event: Event) -> None:
        touched.add(event.data["entity_id"])

    @callback
    def _everything(event_data: object) -> bool:
        return True

    hass.bus.async_listen(EVENT_STATE_CHANGED, _record)
    hass.bus.async_listen(EVENT_STATE_REPORTED, _record, event_filter=_everything)

    mock_api.patch(
        f"{SURFACES_URL}/{SURFACE['id']}", json={"data": {**SURFACE, "brightness": 30}}
    )
    await coordinator.async_set_surface_brightness(SURFACE["id"], 30)
    await hass.async_block_till_done()

    # The four entities of the surface that changed, and nothing else.
    assert touched == {
        "number.test_surface_brightness",
        "switch.test_surface_screensaver",
        "binary_sensor.test_surface_connectivity",
        "sensor.test_surface_page",
    }


async def test_requests_against_one_instance_are_bounded(
    hass: HomeAssistant,
    mock_api: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A burst of writes never has more than MAX_IN_FLIGHT requests open."""
    await setup_entry(hass, mock_config_entry)
    coordinator = mock_config_entry.runtime_data

    in_flight = 0
    peak = 0

    async def slow_patch(
        method: str, url: object, data: object
    ) -> AiohttpClientMockResponse:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0)
        in_flight -= 1
        return AiohttpClientMockResponse(
            method, url, json={"data": {**SURFACE, "brightness": 30}}
        )

    mock_api.patch(f"{SURFACES_URL}/{SURFACE['id']}", side_effect=slow_patch)
    await asyncio.gather(
        *(
            coordinator.async_set_surface_brightness(SURFACE["id"], 30)
            for _ in range(50)
        )
    )

    assert peak <= MAX_IN_FLIGHT
