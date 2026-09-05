"""Fixtures for the Bitfocus Companion tests."""

from __future__ import annotations

from collections.abc import Generator
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.bitfocus_companion.const import DEFAULT_SCAN_INTERVAL, DOMAIN

pytest_plugins = "pytest_homeassistant_custom_component"

HOST = "192.0.2.10"
PORT = 8000
TOKEN = "cpn_write"
BASE = f"http://{HOST}:{PORT}"
OPENAPI_URL = f"{BASE}/api/v2/openapi.json"
SURFACES_URL = f"{BASE}/api/v2/surfaces/v1"
CONNECTIONS_URL = f"{BASE}/api/v2/connections/v1"

ENTRY_DATA = {
    "host": HOST,
    "port": PORT,
    "token": TOKEN,
    "ssl": False,
    "verify_ssl": True,
}

OPENAPI_DOC = {
    "openapi": "3.0.3",
    "info": {"title": "Bitfocus Companion REST API", "version": "5.1.0"},
    "paths": {
        "/surfaces/v1": {"get": {}},
        "/surfaces/v1/{surfaceId}": {"get": {}, "patch": {}},
        "/connections/v1": {"get": {}},
        "/connections/v1/{connectionId}": {"get": {}, "patch": {}},
    },
}

# A future Companion that moved connections to a version this integration does not
# speak, while still serving the surfaces it does.
OPENAPI_DOC_SURFACES_ONLY = {
    **OPENAPI_DOC,
    "paths": {"/surfaces/v1": {"get": {}}, "/connections/v2": {"get": {}}},
}

SURFACE = {
    "id": "emulator:test",
    "type": "Emulator",
    "integrationType": "emulator",
    "name": "Test surface",
    "displayName": "Test surface (emulator:test)",
    "isConnected": True,
    "size": {"rows": 4, "columns": 8},
    "brightness": 100,
    "page": {"id": "page-1", "number": 1, "name": "Main"},
    "groupId": "emulator:test",
}

SURFACE_TWO = {
    **SURFACE,
    "id": "streamdeck:XL1",
    "type": "Elgato Stream Deck XL",
    "integrationType": "elgato-stream-deck",
    "name": "Front of house",
    "displayName": "Front of house (streamdeck:XL1)",
    "brightness": 80,
}

CONNECTION = {
    "id": "conn-1",
    "label": "ATEM",
    "moduleId": "bmd-atem",
    "moduleVersionId": "1.2.0",
    "updatePolicy": "stable",
    "enabled": True,
    "status": {"category": "good", "level": "ok", "message": "Connected"},
    "config": {},
    "secrets": {},
}


ADMIN_UI = "<!doctype html><html><head><title>Companion - Admin</title></head></html>"


def serve(
    mock: AiohttpClientMocker,
    surfaces: list[dict[str, Any]] | None = None,
    connections: list[dict[str, Any]] | None = None,
) -> None:
    """Answer as a healthy Companion with exactly these surfaces and connections."""
    mock.clear_requests()
    mock.get(OPENAPI_URL, json=OPENAPI_DOC)
    mock.get(SURFACES_URL, json=collection([] if surfaces is None else surfaces))
    mock.get(
        CONNECTIONS_URL, json=collection([] if connections is None else connections)
    )


async def setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Add the entry and set it up."""
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def poll(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """Let one polling interval pass."""
    freezer.tick(timedelta(seconds=DEFAULT_SCAN_INTERVAL + 1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)


def collection(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Wrap items the way the Companion API does."""
    return {
        "data": items,
        "meta": {"total": len(items), "limit": len(items), "offset": 0},
    }


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load the integration from custom_components in every test."""
    return


@pytest.fixture
def mock_setup_entry() -> Generator[AsyncMock]:
    """Skip the real setup when only the flow is under test."""
    with patch(
        "custom_components.bitfocus_companion.async_setup_entry", return_value=True
    ) as mocked:
        yield mocked


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return a config entry for the test instance."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=f"{HOST}:{PORT}",
        data=ENTRY_DATA,
        options={},
        unique_id=f"{HOST}:{PORT}",
        entry_id="01JCOMPANIONTESTENTRY0000",
    )


@pytest.fixture
def mock_api(aioclient_mock: AiohttpClientMocker) -> AiohttpClientMocker:
    """Answer every request the integration makes with a healthy Companion."""
    serve(aioclient_mock, [SURFACE, SURFACE_TWO], [CONNECTION])
    return aioclient_mock
