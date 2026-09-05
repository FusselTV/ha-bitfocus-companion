"""Tests for the screensaver switch and the update policy select."""

from __future__ import annotations

from homeassistant.components.number import (
    ATTR_VALUE,
    SERVICE_SET_VALUE,
)
from homeassistant.components.number import (
    DOMAIN as NUMBER_DOMAIN,
)
from homeassistant.components.select import (
    ATTR_OPTION,
    SERVICE_SELECT_OPTION,
)
from homeassistant.components.select import (
    DOMAIN as SELECT_DOMAIN,
)
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
)
from homeassistant.core import HomeAssistant, State
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    mock_restore_cache_with_extra_data,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from .conftest import (
    CONNECTION,
    CONNECTIONS_URL,
    OPENAPI_DOC,
    OPENAPI_URL,
    SURFACE,
    SURFACES_URL,
    collection,
    setup_entry,
)

SCREENSAVER = "switch.test_surface_screensaver"
BRIGHTNESS = "number.test_surface_brightness"
POLICY = "select.atem_module_updates"


async def test_screensaver_dims_and_restores(
    hass: HomeAssistant,
    mock_api: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Turning it on dims to zero; turning it off goes back to what it was."""
    await setup_entry(hass, mock_config_entry)
    assert hass.states.get(SCREENSAVER).state == STATE_OFF
    assert hass.states.get(BRIGHTNESS).state == "100"

    mock_api.patch(
        f"{SURFACES_URL}/{SURFACE['id']}", json={"data": {**SURFACE, "brightness": 0}}
    )
    await hass.services.async_call(
        SWITCH_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: SCREENSAVER}, blocking=True
    )
    _method, _url, data, _headers = mock_api.mock_calls[-1]
    assert data == {"brightness": 0}
    assert hass.states.get(SCREENSAVER).state == STATE_ON
    assert hass.states.get(BRIGHTNESS).state == "0"

    mock_api.clear_requests()
    mock_api.patch(
        f"{SURFACES_URL}/{SURFACE['id']}", json={"data": {**SURFACE, "brightness": 100}}
    )
    await hass.services.async_call(
        SWITCH_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: SCREENSAVER}, blocking=True
    )
    _method, _url, data, _headers = mock_api.mock_calls[-1]
    assert data == {"brightness": 100}
    assert hass.states.get(SCREENSAVER).state == STATE_OFF


async def test_screensaver_remembers_a_dimmed_value(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A surface already at 35% comes back at 35%, not at full."""
    aioclient_mock.get(OPENAPI_URL, json=OPENAPI_DOC)
    aioclient_mock.get(SURFACES_URL, json=collection([{**SURFACE, "brightness": 35}]))
    aioclient_mock.get(CONNECTIONS_URL, json=collection([CONNECTION]))
    await setup_entry(hass, mock_config_entry)

    aioclient_mock.patch(
        f"{SURFACES_URL}/{SURFACE['id']}", json={"data": {**SURFACE, "brightness": 0}}
    )
    await hass.services.async_call(
        SWITCH_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: SCREENSAVER}, blocking=True
    )
    aioclient_mock.clear_requests()
    aioclient_mock.patch(
        f"{SURFACES_URL}/{SURFACE['id']}", json={"data": {**SURFACE, "brightness": 35}}
    )
    await hass.services.async_call(
        SWITCH_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: SCREENSAVER}, blocking=True
    )
    _method, _url, data, _headers = aioclient_mock.mock_calls[-1]
    assert data == {"brightness": 35}


async def test_screensaver_falls_back_to_full_brightness(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A surface dimmed to zero elsewhere still has a way back."""
    aioclient_mock.get(OPENAPI_URL, json=OPENAPI_DOC)
    aioclient_mock.get(SURFACES_URL, json=collection([{**SURFACE, "brightness": 0}]))
    aioclient_mock.get(CONNECTIONS_URL, json=collection([CONNECTION]))
    await setup_entry(hass, mock_config_entry)

    assert hass.states.get(SCREENSAVER).state == STATE_ON

    aioclient_mock.patch(
        f"{SURFACES_URL}/{SURFACE['id']}", json={"data": {**SURFACE, "brightness": 100}}
    )
    await hass.services.async_call(
        SWITCH_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: SCREENSAVER}, blocking=True
    )
    _method, _url, data, _headers = aioclient_mock.mock_calls[-1]
    assert data == {"brightness": 100}


async def test_screensaver_survives_a_restart(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """The saved brightness is restored with the entity."""
    mock_restore_cache_with_extra_data(
        hass, ((State(SCREENSAVER, STATE_ON), {"brightness": 60}),)
    )
    aioclient_mock.get(OPENAPI_URL, json=OPENAPI_DOC)
    aioclient_mock.get(SURFACES_URL, json=collection([{**SURFACE, "brightness": 0}]))
    aioclient_mock.get(CONNECTIONS_URL, json=collection([CONNECTION]))
    await setup_entry(hass, mock_config_entry)

    aioclient_mock.patch(
        f"{SURFACES_URL}/{SURFACE['id']}", json={"data": {**SURFACE, "brightness": 60}}
    )
    await hass.services.async_call(
        SWITCH_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: SCREENSAVER}, blocking=True
    )
    _method, _url, data, _headers = aioclient_mock.mock_calls[-1]
    assert data == {"brightness": 60}


async def test_update_policy_select(
    hass: HomeAssistant,
    mock_api: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """The update policy can be read and changed."""
    await setup_entry(hass, mock_config_entry)
    assert hass.states.get(POLICY).state == "stable"

    mock_api.patch(
        f"{CONNECTIONS_URL}/{CONNECTION['id']}",
        json={"data": {**CONNECTION, "updatePolicy": "manual"}},
    )
    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: POLICY, ATTR_OPTION: "manual"},
        blocking=True,
    )
    _method, _url, data, _headers = mock_api.mock_calls[-1]
    assert data == {"updatePolicy": "manual"}
    assert hass.states.get(POLICY).state == "manual"


async def test_the_restore_value_survives_a_wake(
    hass: HomeAssistant,
    mock_api: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Sleeping, waking and dimming again must still come back to the same value."""
    await setup_entry(hass, mock_config_entry)

    async def set_brightness(value: int) -> None:
        mock_api.clear_requests()
        mock_api.patch(
            f"{SURFACES_URL}/{SURFACE['id']}",
            json={"data": {**SURFACE, "brightness": value}},
        )
        await hass.services.async_call(
            NUMBER_DOMAIN,
            SERVICE_SET_VALUE,
            {ATTR_ENTITY_ID: BRIGHTNESS, ATTR_VALUE: value},
            blocking=True,
        )

    async def screensaver(service: str, restores_to: int) -> None:
        mock_api.clear_requests()
        mock_api.patch(
            f"{SURFACES_URL}/{SURFACE['id']}",
            json={"data": {**SURFACE, "brightness": restores_to}},
        )
        await hass.services.async_call(
            SWITCH_DOMAIN, service, {ATTR_ENTITY_ID: SCREENSAVER}, blocking=True
        )

    await set_brightness(60)
    await screensaver(SERVICE_TURN_ON, 0)
    await screensaver(SERVICE_TURN_OFF, 60)

    # Dimmed to nothing again before the next poll, without touching the switch.
    await set_brightness(0)
    assert hass.states.get(SCREENSAVER).state == STATE_ON

    mock_api.clear_requests()
    mock_api.patch(
        f"{SURFACES_URL}/{SURFACE['id']}", json={"data": {**SURFACE, "brightness": 60}}
    )
    await hass.services.async_call(
        SWITCH_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: SCREENSAVER}, blocking=True
    )
    _method, _url, data, _headers = mock_api.mock_calls[-1]
    assert data == {"brightness": 60}
