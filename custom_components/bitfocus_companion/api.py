"""Async client for the Bitfocus Companion REST API."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from http import HTTPStatus
from json import loads as json_loads
from typing import Any, Final

import aiohttp
from yarl import URL

API_BASE: Final = "/api/v2"
OPENAPI_PATH: Final = f"{API_BASE}/openapi.json"
SURFACES_PATH: Final = f"{API_BASE}/surfaces/v1"
CONNECTIONS_PATH: Final = f"{API_BASE}/connections/v1"

REQUEST_TIMEOUT: Final = aiohttp.ClientTimeout(total=10)

# Companion is a single process, and Home Assistant shares 100 connections per host
# with every other integration. A service call on a hundred surfaces should wait in
# line here instead of opening a hundred sockets.
MAX_IN_FLIGHT: Final = 8

_LOGGER = logging.getLogger(__name__)

# Companion answers 404 for any unknown path under /api, but serves its web app for
# everything else. The page title is the only way to tell another web server apart
# from a Companion running without the REST API.
_ADMIN_UI_MARKER: Final = "Companion - Admin"


class CompanionError(Exception):
    """Base error for the Companion API."""


class CompanionConnectionError(CompanionError):
    """The Companion instance could not be reached."""


class CompanionNotFoundError(CompanionError):
    """Something answered, but it is not a Companion instance."""


class CompanionApiUnavailableError(CompanionError):
    """Companion is running, but its REST API is not mounted.

    Either EXPERIMENTAL_ENABLE_REST_API is unset or the build predates the API.
    """


class CompanionApiVersionError(CompanionError):
    """Companion has a REST API, but not the resource versions this client uses."""

    def __init__(self, version: str, missing: str) -> None:
        """Keep what is missing so the UI can name it."""
        super().__init__(f"Companion {version} does not serve {missing}")
        self.version = version
        self.missing = missing


class CompanionAuthError(CompanionError):
    """The API token was rejected."""


class CompanionScopeError(CompanionError):
    """The API token is valid but lacks a scope the integration needs."""


class CompanionResponseError(CompanionError):
    """Companion rejected a request or answered with something unusable."""


@dataclass(frozen=True, slots=True)
class ApiCapabilities:
    """Which resource versions a Companion instance actually serves.

    Companion versions every resource on its own, and its contract test refuses any
    change that removes or narrows one. So asking the OpenAPI document what is there
    is more reliable than guessing from the application version.
    """

    version: str
    connections: bool


@dataclass(frozen=True, slots=True)
class SurfacePage:
    """The page a surface is currently showing."""

    id: str
    number: int | None
    name: str | None


@dataclass(frozen=True, slots=True)
class Surface:
    """A surface known to Companion."""

    id: str
    type: str
    integration_type: str
    name: str
    display_name: str
    is_connected: bool
    rows: int | None
    columns: int | None
    brightness: int | None
    page: SurfacePage | None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Surface:
        """Build a surface from an API payload."""
        size = data.get("size") or {}
        page = data.get("page")
        return cls(
            id=str(data["id"]),
            type=str(data.get("type") or ""),
            integration_type=str(data.get("integrationType") or ""),
            name=str(data.get("name") or ""),
            display_name=str(data.get("displayName") or data["id"]),
            is_connected=bool(data.get("isConnected")),
            rows=size.get("rows"),
            columns=size.get("columns"),
            brightness=data.get("brightness"),
            page=(
                SurfacePage(
                    id=str(page["id"]),
                    number=page.get("number"),
                    name=page.get("name"),
                )
                if page
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class ConnectionStatus:
    """Status reported by a connection module."""

    category: str | None
    level: str | None
    message: str | None


@dataclass(frozen=True, slots=True)
class Connection:
    """A module connection configured in Companion."""

    id: str
    label: str
    module_id: str
    module_version_id: str | None
    update_policy: str | None
    enabled: bool
    status: ConnectionStatus | None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Connection:
        """Build a connection from an API payload."""
        status = data.get("status")
        return cls(
            id=str(data["id"]),
            label=str(data.get("label") or data["id"]),
            module_id=str(data.get("moduleId") or ""),
            module_version_id=data.get("moduleVersionId"),
            update_policy=data.get("updatePolicy"),
            enabled=bool(data.get("enabled")),
            status=(
                ConnectionStatus(
                    category=status.get("category"),
                    level=status.get("level"),
                    message=status.get("message"),
                )
                if status
                else None
            ),
        )


class CompanionClient:
    """Talk to one Companion instance."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        base_url: URL,
        token: str,
        *,
        verify_ssl: bool,
    ) -> None:
        """Initialise the client with an injected websession."""
        self._session = session
        self._base_url = base_url
        self._token = token
        self._in_flight = asyncio.Semaphore(MAX_IN_FLIGHT)
        # aiohttp needs a real bool here. It only matters for https.
        self._ssl: bool = verify_ssl if base_url.scheme == "https" else True

    @property
    def base_url(self) -> URL:
        """Return the base URL of the Companion instance."""
        return self._base_url

    async def async_get_capabilities(self) -> ApiCapabilities:
        """Return the version and the resources this Companion serves.

        Every way this can fail raises its own error, so the config flow can tell the
        user which one it was.
        """
        try:
            async with self._in_flight:
                response = await self._session.get(
                    self._base_url.join(URL(OPENAPI_PATH)),
                    timeout=REQUEST_TIMEOUT,
                    ssl=self._ssl,
                )
                body = await response.text()
            if response.status in (
                HTTPStatus.NOT_FOUND,
                HTTPStatus.UNAUTHORIZED,
                HTTPStatus.FORBIDDEN,
            ):
                # This path needs no token, so anything but a 200 here means the REST
                # API is not the thing answering. Companion's legacy API replies 403
                # when it is switched off, and 404 when it is on but has no such path.
                await self._async_raise_for_missing_api()
            if response.status != HTTPStatus.OK:
                raise CompanionResponseError(
                    f"Unexpected status {response.status} from {OPENAPI_PATH}"
                )
            payload = json_loads(body)
        except (TimeoutError, aiohttp.ClientError) as err:
            raise CompanionConnectionError(str(err)) from err
        except ValueError as err:
            raise CompanionNotFoundError("Response was not JSON") from err

        if not isinstance(payload, dict) or "Companion" not in str(
            payload.get("info", {}).get("title", "")
        ):
            raise CompanionNotFoundError("Not a Companion OpenAPI document")

        version = str(payload.get("info", {}).get("version") or "")
        paths = payload.get("paths")
        if not isinstance(paths, dict):
            raise CompanionResponseError("OpenAPI document has no paths")
        if SURFACES_PATH.removeprefix(API_BASE) not in paths:
            raise CompanionApiVersionError(version, SURFACES_PATH)
        return ApiCapabilities(
            version=version,
            connections=CONNECTIONS_PATH.removeprefix(API_BASE) in paths,
        )

    async def _async_raise_for_missing_api(self) -> None:
        """Work out why /api/v2 is missing and raise the matching error."""
        try:
            async with self._in_flight:
                response = await self._session.get(
                    self._base_url,
                    timeout=REQUEST_TIMEOUT,
                    ssl=self._ssl,
                )
                body = await response.text()
        except (TimeoutError, aiohttp.ClientError) as err:
            raise CompanionConnectionError(str(err)) from err

        if _ADMIN_UI_MARKER in body:
            raise CompanionApiUnavailableError(
                "Companion is running without the REST API"
            )
        raise CompanionNotFoundError("No Companion admin UI on this address")

    async def async_get_surfaces(self) -> list[Surface]:
        """Return every surface Companion knows about."""
        payload = await self._async_request("GET", SURFACES_PATH)
        return self._parse_items(payload, Surface.from_json, "surface")

    async def async_get_connections(self) -> list[Connection]:
        """Return every connection configured in Companion."""
        payload = await self._async_request("GET", CONNECTIONS_PATH)
        return self._parse_items(payload, Connection.from_json, "connection")

    @staticmethod
    def _parse_one[T](
        payload: dict[str, Any], factory: Callable[[dict[str, Any]], T]
    ) -> T:
        """Parse the single object a write returns."""
        item = payload.get("data")
        if not isinstance(item, dict):
            raise CompanionResponseError("Companion answered without an object")
        try:
            return factory(item)
        except (AttributeError, KeyError, TypeError, ValueError) as err:
            raise CompanionResponseError(f"Unreadable response: {err}") from err

    @staticmethod
    def _parse_items[T](
        payload: dict[str, Any],
        factory: Callable[[dict[str, Any]], T],
        kind: str,
    ) -> list[T]:
        """Parse a collection, dropping entries Companion describes in a new way.

        One unreadable entry should cost that one device, not the whole poll.
        """
        items: list[T] = []
        data = payload.get("data")
        if not isinstance(data, list):
            raise CompanionResponseError("Companion answered without a collection")
        for item in data:
            try:
                items.append(factory(item))
            except (AttributeError, KeyError, TypeError, ValueError):
                _LOGGER.warning(
                    "Skipping a %s this version of the integration cannot read: %s",
                    kind,
                    item,
                )
        return items

    async def async_set_surface_brightness(
        self, surface_id: str, brightness: int
    ) -> Surface:
        """Set the brightness of a surface, in percent."""
        payload = await self._async_request(
            "PATCH",
            f"{SURFACES_PATH}/{surface_id}",
            json={"brightness": brightness},
        )
        return self._parse_one(payload, Surface.from_json)

    async def async_set_connection_enabled(
        self, connection_id: str, enabled: bool
    ) -> Connection:
        """Enable or disable a connection."""
        payload = await self._async_request(
            "PATCH",
            f"{CONNECTIONS_PATH}/{connection_id}",
            json={"disabled": not enabled},
        )
        return self._parse_one(payload, Connection.from_json)

    async def async_set_connection_update_policy(
        self, connection_id: str, policy: str
    ) -> Connection:
        """Set the module version update policy of a connection."""
        payload = await self._async_request(
            "PATCH",
            f"{CONNECTIONS_PATH}/{connection_id}",
            json={"updatePolicy": policy},
        )
        return self._parse_one(payload, Connection.from_json)

    async def async_restart_connection(self, connection_id: str) -> None:
        """Restart a connection."""
        await self._async_request("POST", f"{CONNECTIONS_PATH}/{connection_id}/restart")

    async def _async_request(
        self, method: str, path: str, *, json: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Perform an authenticated API request and unwrap the response body."""
        try:
            async with self._in_flight:
                response = await self._session.request(
                    method,
                    self._base_url.join(URL(path)),
                    json=json,
                    headers={"Authorization": f"Bearer {self._token}"},
                    timeout=REQUEST_TIMEOUT,
                    ssl=self._ssl,
                )
                body = await response.text()
        except (TimeoutError, aiohttp.ClientError) as err:
            raise CompanionConnectionError(str(err)) from err

        if response.status == HTTPStatus.UNAUTHORIZED:
            raise CompanionAuthError(_error_message(body) or "Token rejected")
        if response.status == HTTPStatus.FORBIDDEN:
            raise CompanionScopeError(_error_message(body) or "Token scope too narrow")
        if response.status == HTTPStatus.NOT_FOUND:
            # A 404 here has two meanings. Either the id is unknown, or the whole
            # API is gone because Companion restarted without the flag.
            await self._async_raise_for_missing_api_or_id(body)
        if response.status >= HTTPStatus.BAD_REQUEST:
            raise CompanionResponseError(
                _error_message(body) or f"Companion returned {response.status}"
            )

        if not body:
            return {}
        try:
            payload: Any = json_loads(body)
        except ValueError as err:
            raise CompanionResponseError("Response was not JSON") from err
        if not isinstance(payload, dict):
            raise CompanionResponseError("Unexpected response shape")
        body_json: dict[str, Any] = payload
        return body_json

    async def _async_raise_for_missing_api_or_id(self, body: str) -> None:
        """Tell "no such id" apart from "the REST API is gone"."""
        if _error_payload(body).get("code"):
            raise CompanionResponseError(_error_message(body) or "Not found")
        await self._async_raise_for_missing_api()


def _error_payload(body: str) -> dict[str, Any]:
    """Return the error object of a Companion error response, if there is one."""
    try:
        parsed: Any = json_loads(body)
    except ValueError:
        return {}
    if isinstance(parsed, dict):
        error = parsed.get("error")
        if isinstance(error, dict):
            payload: dict[str, Any] = error
            return payload
    return {}


def _error_message(body: str) -> str | None:
    """Return the human-readable message of a Companion error response."""
    message = _error_payload(body).get("message")
    return str(message) if message else None
