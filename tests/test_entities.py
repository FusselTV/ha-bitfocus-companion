"""Tests for the entities the integration exposes."""

from __future__ import annotations

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN
from homeassistant.components.button import SERVICE_PRESS
from homeassistant.components.number import (
    ATTR_VALUE,
    SERVICE_SET_VALUE,
)
from homeassistant.components.number import (
    DOMAIN as NUMBER_DOMAIN,
)
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.bitfocus_companion.const import CONF_EXCLUDED_SURFACES, DOMAIN

from .conftest import (
    CONNECTION,
    CONNECTIONS_URL,
    OPENAPI_DOC,
    OPENAPI_URL,
    SURFACE,
    SURFACE_TWO,
    SURFACES_URL,
    collection,
    poll,
    serve,
    setup_entry,
)

BRIGHTNESS = "number.test_surface_brightness"
PAGE = "sensor.test_surface_page"
CONNECTED = "binary_sensor.test_surface_connectivity"
STATUS = "sensor.atem_status"
STATUS_MESSAGE = "sensor.atem_status_message"
PROBLEM = "binary_sensor.atem_problem"
ENABLED = "switch.atem_enabled"
RESTART = "button.atem_restart"


async def test_entity_states(
    hass: HomeAssistant,
    mock_api: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Every entity reflects what Companion reported."""
    await setup_entry(hass, mock_config_entry)

    assert hass.states.get(BRIGHTNESS).state == "100"
    assert hass.states.get(CONNECTED).state == STATE_ON

    page = hass.states.get(PAGE)
    assert page.state == "1"
    assert page.attributes["page_name"] == "Main"
    assert page.attributes["page_id"] == "page-1"

    assert hass.states.get(STATUS).state == "good"
    assert hass.states.get(STATUS_MESSAGE).state == "Connected"
    assert hass.states.get(STATUS_MESSAGE).attributes["level"] == "ok"
    assert hass.states.get(PROBLEM).state == STATE_OFF
    assert hass.states.get(ENABLED).state == STATE_ON
    assert hass.states.get(RESTART).state is not None


async def test_excluded_surface_has_no_entities(
    hass: HomeAssistant,
    mock_api: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A surface the user unticked never becomes an entity."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, options={CONF_EXCLUDED_SURFACES: [SURFACE_TWO["id"]]}
    )
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get(BRIGHTNESS) is not None
    assert hass.states.get("number.front_of_house_brightness") is None


async def test_setting_brightness(
    hass: HomeAssistant,
    mock_api: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Setting brightness patches the surface and updates the state at once."""
    await setup_entry(hass, mock_config_entry)
    mock_api.patch(
        f"{SURFACES_URL}/{SURFACE['id']}", json={"data": {**SURFACE, "brightness": 42}}
    )

    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: BRIGHTNESS, ATTR_VALUE: 42},
        blocking=True,
    )

    method, url, data, _headers = mock_api.mock_calls[-1]
    assert method == "PATCH"
    assert url.path == f"/api/v2/surfaces/v1/{SURFACE['id']}"
    assert data == {"brightness": 42}
    assert hass.states.get(BRIGHTNESS).state == "42"


async def test_toggling_a_connection(
    hass: HomeAssistant,
    mock_api: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """The switch sends the inverted "disabled" flag Companion expects."""
    await setup_entry(hass, mock_config_entry)
    mock_api.patch(
        f"{CONNECTIONS_URL}/{CONNECTION['id']}",
        json={"data": {**CONNECTION, "enabled": False}},
    )

    await hass.services.async_call(
        SWITCH_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: ENABLED}, blocking=True
    )
    _method, _url, data, _headers = mock_api.mock_calls[-1]
    assert data == {"disabled": True}
    assert hass.states.get(ENABLED).state == STATE_OFF
    assert hass.states.get(PROBLEM).state == STATE_OFF

    mock_api.clear_requests()
    mock_api.patch(
        f"{CONNECTIONS_URL}/{CONNECTION['id']}",
        json={"data": {**CONNECTION, "enabled": True}},
    )
    await hass.services.async_call(
        SWITCH_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: ENABLED}, blocking=True
    )
    _method, _url, data, _headers = mock_api.mock_calls[-1]
    assert data == {"disabled": False}


async def test_restart_button(
    hass: HomeAssistant,
    mock_api: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Pressing restart posts to the restart endpoint."""
    await setup_entry(hass, mock_config_entry)
    mock_api.post(f"{CONNECTIONS_URL}/{CONNECTION['id']}/restart", text="")

    await hass.services.async_call(
        BUTTON_DOMAIN, SERVICE_PRESS, {ATTR_ENTITY_ID: RESTART}, blocking=True
    )
    posts = [
        url.path
        for method, url, _data, _headers in mock_api.mock_calls
        if method == "POST"
    ]
    assert posts == [f"/api/v2/connections/v1/{CONNECTION['id']}/restart"]


async def test_write_failure_surfaces_as_an_error(
    hass: HomeAssistant,
    mock_api: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A rejected write reaches the user as a Home Assistant error."""
    await setup_entry(hass, mock_config_entry)
    mock_api.patch(
        f"{SURFACES_URL}/{SURFACE['id']}",
        status=400,
        json={"error": {"code": "BAD_REQUEST", "message": "Invalid request body"}},
    )

    with pytest.raises(HomeAssistantError, match="Invalid request body"):
        await hass.services.async_call(
            NUMBER_DOMAIN,
            SERVICE_SET_VALUE,
            {ATTR_ENTITY_ID: BRIGHTNESS, ATTR_VALUE: 10},
            blocking=True,
        )


async def test_new_surface_appears(
    hass: HomeAssistant,
    mock_api: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A surface plugged in later shows up without a reload."""
    await setup_entry(hass, mock_config_entry)
    assert hass.states.get("number.late_arrival_brightness") is None

    late = {**SURFACE, "id": "streamdeck:LATE", "name": "Late arrival"}
    serve(mock_api, [SURFACE, SURFACE_TWO, late], [CONNECTION])

    await poll(hass, freezer)

    assert hass.states.get("number.late_arrival_brightness").state == "100"


async def test_entities_go_away_when_a_surface_disappears(
    hass: HomeAssistant,
    mock_api: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A surface removed in Companion takes its device and entities with it."""
    await setup_entry(hass, mock_config_entry)
    assert hass.states.get(BRIGHTNESS).state == "100"

    serve(mock_api, [SURFACE_TWO], [CONNECTION])

    await poll(hass, freezer)
    await poll(hass, freezer)

    assert hass.states.get(BRIGHTNESS) is None


async def test_unknown_status_category_is_clamped(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A category Companion invents later does not break the enum sensor."""
    aioclient_mock.get(OPENAPI_URL, json=OPENAPI_DOC)
    aioclient_mock.get(SURFACES_URL, json=collection([SURFACE]))
    aioclient_mock.get(
        CONNECTIONS_URL,
        json=collection(
            [
                {
                    **CONNECTION,
                    "status": {
                        "category": "brand-new",
                        "level": "?",
                        "message": "Hello",
                    },
                }
            ]
        ),
    )
    await setup_entry(hass, mock_config_entry)

    assert hass.states.get(STATUS).state == "unknown"
    assert hass.states.get(PROBLEM).state == STATE_ON


async def test_unticking_a_surface_removes_its_device(
    hass: HomeAssistant,
    mock_api: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Deselecting a surface in the options takes its device with it."""
    await setup_entry(hass, mock_config_entry)
    registry = dr.async_get(hass)
    device_id = f"{mock_config_entry.entry_id}_surface_{SURFACE_TWO['id']}"
    assert registry.async_get_device(identifiers={(DOMAIN, device_id)}) is not None

    hass.config_entries.async_update_entry(
        mock_config_entry,
        options={CONF_EXCLUDED_SURFACES: [SURFACE_TWO["id"]]},
    )
    await hass.config_entries.async_reload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert registry.async_get_device(identifiers={(DOMAIN, device_id)}) is None
    assert hass.states.get("number.front_of_house_brightness") is None
    assert hass.states.get(BRIGHTNESS) is not None


async def test_the_two_kinds_of_switch_stay_distinguishable(
    hass: HomeAssistant,
    mock_api: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """The blueprint picks surfaces by the absence of a device class."""
    await setup_entry(hass, mock_config_entry)

    screensaver = hass.states.get("switch.test_surface_screensaver")
    connection = hass.states.get(ENABLED)
    assert screensaver.attributes.get("device_class") is None
    assert connection.attributes["device_class"] == "switch"
