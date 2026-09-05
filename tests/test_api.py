"""Tests for the Companion API client."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
from yarl import URL

from custom_components.bitfocus_companion.api import (
    CompanionApiUnavailableError,
    CompanionApiVersionError,
    CompanionAuthError,
    CompanionClient,
    CompanionConnectionError,
    CompanionNotFoundError,
    CompanionResponseError,
    CompanionScopeError,
)

from .conftest import (
    ADMIN_UI,
    BASE,
    CONNECTION,
    CONNECTIONS_URL,
    OPENAPI_DOC_SURFACES_ONLY,
    OPENAPI_URL,
    SURFACE,
    SURFACES_URL,
    collection,
)


@pytest.fixture
def client(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> CompanionClient:
    """Return a client pointed at the test instance."""
    return CompanionClient(
        async_get_clientsession(hass), URL(BASE), "cpn_write", verify_ssl=True
    )


async def test_version_and_collections(
    client: CompanionClient, mock_api: AiohttpClientMocker
) -> None:
    """A healthy instance yields its version and both collections."""
    capabilities = await client.async_get_capabilities()
    assert capabilities.version == "5.1.0"
    assert capabilities.connections is True
    surfaces = await client.async_get_surfaces()
    assert [surface.id for surface in surfaces] == [SURFACE["id"], "streamdeck:XL1"]
    assert surfaces[0].page is not None
    assert surfaces[0].page.number == 1
    connections = await client.async_get_connections()
    assert connections[0].status is not None
    assert connections[0].status.category == "good"


async def test_surface_without_page_or_size(
    client: CompanionClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Missing optional fields do not blow up the parser."""
    aioclient_mock.get(
        SURFACES_URL,
        json=collection([{**SURFACE, "page": None, "size": None, "brightness": None}]),
    )
    surface = (await client.async_get_surfaces())[0]
    assert surface.page is None
    assert surface.rows is None
    assert surface.brightness is None


async def test_connection_without_status(
    client: CompanionClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """A connection with no status yet parses to None."""
    aioclient_mock.get(
        CONNECTIONS_URL, json=collection([{**CONNECTION, "status": None}])
    )
    assert (await client.async_get_connections())[0].status is None


async def test_unreachable(
    client: CompanionClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """A timeout is reported as a connection error."""
    aioclient_mock.get(OPENAPI_URL, exc=TimeoutError())
    with pytest.raises(CompanionConnectionError):
        await client.async_get_capabilities()


async def test_unreachable_while_probing_the_ui(
    client: CompanionClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Losing the host halfway through the probe is still a connection error."""
    aioclient_mock.get(OPENAPI_URL, status=404, text="Not found")
    aioclient_mock.get(f"{BASE}/", exc=TimeoutError())
    with pytest.raises(CompanionConnectionError):
        await client.async_get_capabilities()


async def test_api_switched_off(
    client: CompanionClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """A running Companion without the REST API is its own error."""
    aioclient_mock.get(OPENAPI_URL, status=404, text="Not found")
    aioclient_mock.get(f"{BASE}/", status=200, text=ADMIN_UI)
    with pytest.raises(CompanionApiUnavailableError):
        await client.async_get_capabilities()


async def test_some_other_server(
    client: CompanionClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Another web server on that port is not Companion."""
    aioclient_mock.get(OPENAPI_URL, status=404, text="Not found")
    aioclient_mock.get(f"{BASE}/", status=200, text="<html><title>nginx</title></html>")
    with pytest.raises(CompanionNotFoundError):
        await client.async_get_capabilities()


async def test_unexpected_status_from_openapi(
    client: CompanionClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Anything but 200 or 404 from the spec is a response error."""
    aioclient_mock.get(OPENAPI_URL, status=500, text="boom")
    with pytest.raises(CompanionResponseError):
        await client.async_get_capabilities()


async def test_openapi_is_not_json(
    client: CompanionClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """A 200 that is not JSON means this is not Companion."""
    aioclient_mock.get(OPENAPI_URL, text="hello")
    with pytest.raises(CompanionNotFoundError):
        await client.async_get_capabilities()


async def test_openapi_of_another_product(
    client: CompanionClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Someone else's OpenAPI document is not Companion either."""
    aioclient_mock.get(
        OPENAPI_URL, json={"info": {"title": "Some API", "version": "1"}}
    )
    with pytest.raises(CompanionNotFoundError):
        await client.async_get_capabilities()


async def test_auth_and_scope_errors(
    client: CompanionClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """401 and 403 are told apart."""
    aioclient_mock.get(
        SURFACES_URL, status=401, json={"error": {"code": "UNAUTHORIZED"}}
    )
    with pytest.raises(CompanionAuthError):
        await client.async_get_surfaces()

    aioclient_mock.clear_requests()
    aioclient_mock.get(
        SURFACES_URL,
        status=403,
        json={"error": {"code": "FORBIDDEN", "message": "Insufficient scope"}},
    )
    with pytest.raises(CompanionScopeError, match="Insufficient scope"):
        await client.async_get_surfaces()


async def test_unknown_id_is_not_a_missing_api(
    client: CompanionClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """A 404 carrying an error code is about the id, not the router."""
    aioclient_mock.patch(
        f"{SURFACES_URL}/nope",
        status=404,
        json={"error": {"code": "NOT_FOUND", "message": "Surface not found"}},
    )
    with pytest.raises(CompanionResponseError, match="Surface not found"):
        await client.async_set_surface_brightness("nope", 50)


async def test_404_without_a_code_means_the_api_went_away(
    client: CompanionClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """A bare 404 on a write is the router being gone."""
    aioclient_mock.patch(
        f"{SURFACES_URL}/{SURFACE['id']}", status=404, text="Not found"
    )
    aioclient_mock.get(f"{BASE}/", status=200, text=ADMIN_UI)
    with pytest.raises(CompanionApiUnavailableError):
        await client.async_set_surface_brightness(SURFACE["id"], 50)


async def test_empty_and_broken_bodies(
    client: CompanionClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """An empty body is fine; a non-object body is not."""
    aioclient_mock.post(f"{CONNECTIONS_URL}/{CONNECTION['id']}/restart", text="")
    await client.async_restart_connection(CONNECTION["id"])

    aioclient_mock.clear_requests()
    aioclient_mock.get(
        SURFACES_URL, text="[1, 2]", headers={"content-type": "application/json"}
    )
    with pytest.raises(CompanionResponseError):
        await client.async_get_surfaces()


async def test_write_helpers(
    client: CompanionClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """The write helpers return the updated object."""
    aioclient_mock.patch(
        f"{SURFACES_URL}/{SURFACE['id']}", json={"data": {**SURFACE, "brightness": 25}}
    )
    assert (
        await client.async_set_surface_brightness(SURFACE["id"], 25)
    ).brightness == 25

    aioclient_mock.patch(
        f"{CONNECTIONS_URL}/{CONNECTION['id']}",
        json={"data": {**CONNECTION, "enabled": False}},
    )
    assert not (
        await client.async_set_connection_enabled(CONNECTION["id"], False)
    ).enabled


async def test_https_uses_verify_ssl(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The TLS switch is passed to aiohttp only for https."""
    plain = CompanionClient(
        async_get_clientsession(hass), URL("http://host:8000"), "t", verify_ssl=False
    )
    secure = CompanionClient(
        async_get_clientsession(hass), URL("https://host:8000"), "t", verify_ssl=False
    )
    assert plain._ssl is True
    assert secure._ssl is False


async def test_a_companion_without_our_surface_version(
    client: CompanionClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """A Companion that moved surfaces to another version is named as such."""
    aioclient_mock.get(
        OPENAPI_URL,
        json={
            "info": {"title": "Bitfocus Companion REST API", "version": "7.0.0"},
            "paths": {"/surfaces/v2": {"get": {}}},
        },
    )
    with pytest.raises(CompanionApiVersionError) as err:
        await client.async_get_capabilities()
    assert err.value.version == "7.0.0"
    assert "surfaces/v1" in err.value.missing


async def test_connections_can_be_absent(
    client: CompanionClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Surfaces keep working when the connections resource is a version we skip."""
    aioclient_mock.get(OPENAPI_URL, json=OPENAPI_DOC_SURFACES_ONLY)
    capabilities = await client.async_get_capabilities()
    assert capabilities.connections is False


async def test_an_unreadable_item_is_skipped(
    client: CompanionClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """One entry described in a way we cannot read costs that entry, not the poll."""
    aioclient_mock.get(
        SURFACES_URL,
        json=collection([SURFACE, {"no_id_here": True}, {**SURFACE, "id": "second"}]),
    )
    surfaces = await client.async_get_surfaces()
    assert [surface.id for surface in surfaces] == [SURFACE["id"], "second"]


async def test_new_fields_are_ignored(
    client: CompanionClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Fields Companion adds later do not upset the parser."""
    aioclient_mock.get(
        SURFACES_URL,
        json=collection([{**SURFACE, "somethingNew": {"nested": [1, 2, 3]}}]),
    )
    assert (await client.async_get_surfaces())[0].id == SURFACE["id"]


async def test_a_prerelease_is_accepted(
    client: CompanionClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """What matters is the resource being served, not how the version is spelled."""
    aioclient_mock.get(
        OPENAPI_URL,
        json={
            "info": {"title": "Bitfocus Companion REST API", "version": "5.1.0-beta.1"},
            "paths": {"/surfaces/v1": {"get": {}}},
        },
    )
    capabilities = await client.async_get_capabilities()
    assert capabilities.version == "5.1.0-beta.1"
    assert capabilities.connections is False


async def test_a_write_answering_with_nothing(
    client: CompanionClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """An empty or shapeless write response is a Companion error, not a KeyError."""
    aioclient_mock.patch(f"{SURFACES_URL}/{SURFACE['id']}", text="")
    with pytest.raises(CompanionResponseError):
        await client.async_set_surface_brightness(SURFACE["id"], 50)

    aioclient_mock.clear_requests()
    aioclient_mock.patch(f"{SURFACES_URL}/{SURFACE['id']}", json={"data": None})
    with pytest.raises(CompanionResponseError):
        await client.async_set_surface_brightness(SURFACE["id"], 50)


async def test_a_collection_that_is_not_a_collection(
    client: CompanionClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """A null collection is a Companion error, not a TypeError out of the poll."""
    aioclient_mock.get(SURFACES_URL, json={"data": None})
    with pytest.raises(CompanionResponseError):
        await client.async_get_surfaces()
