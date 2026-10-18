"""Per-entry runtime objects and the LAN callback server shared by all entries."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from aioecam.cloud.api import AylaCloud
from aioecam.device import EcamDevice
from aioecam.lan.link import LanLink
from aioecam.lan.server import LanServer
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN

if TYPE_CHECKING:
    from .coordinator import EcamCoordinator
    from .recipe_reader import RecipeReader
    from .statistics_coordinator import StatisticsCoordinator


@dataclass
class RuntimeData:
    device: EcamDevice
    link: LanLink
    coordinator: EcamCoordinator
    statistics_coordinator: StatisticsCoordinator | None
    cloud: AylaCloud | None
    recipe_reader: RecipeReader | None = None


@dataclass
class _Shared:
    server: LanServer
    users: set[str] = field(default_factory=set)
    remove_stop_listener: Callable[[], None] | None = None


_LOCK = asyncio.Lock()


async def async_acquire_server(
    hass: HomeAssistant, entry_id: str, port: int, advertise_ip: str | None
) -> LanServer:
    """Start the callback server on first use; later entries share it."""
    async with _LOCK:
        shared: _Shared | None = hass.data.setdefault(DOMAIN, {}).get("lan")
        if shared is None:
            server = LanServer(async_get_clientsession(hass), port=port, advertise_ip=advertise_ip)
            await server.start()
            shared = _Shared(server)
            hass.data[DOMAIN]["lan"] = shared

            async def _close_on_stop(_event: Event) -> None:
                # Entries are not unloaded on shutdown; stop the links before
                # Home Assistant closes the shared HTTP session under them.
                if shared is not None:
                    shared.remove_stop_listener = None  # already fired
                await server.close()

            shared.remove_stop_listener = hass.bus.async_listen_once(
                EVENT_HOMEASSISTANT_STOP, _close_on_stop
            )
        shared.users.add(entry_id)
        return shared.server


async def async_release_server(hass: HomeAssistant, entry_id: str) -> None:
    """Stop the callback server when the last entry is unloaded."""
    async with _LOCK:
        shared: _Shared | None = hass.data.get(DOMAIN, {}).get("lan")
        if shared is None:
            return
        shared.users.discard(entry_id)
        if not shared.users:
            if shared.remove_stop_listener:
                shared.remove_stop_listener()
            await shared.server.close()
            hass.data[DOMAIN].pop("lan", None)
