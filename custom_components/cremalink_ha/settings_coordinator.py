"""Writable machine settings (auto-off, water hardness).

Settings are read with ``0x95`` and work in standby too, so they are polled
slowly and independently of the machine state. Writes go through aioecam,
which announces the write session and confirms each write by reading it back.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import timedelta

from aioecam.device import EcamDevice
from aioecam.exceptions import EcamError
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Settings can only be changed at the display while the machine is awake, so
# follow them closely then; in standby a slow poll is enough. A read lost
# while the machine is busy (e.g. rinsing) is retried in the next round.
SETTINGS_INTERVAL_AWAKE = timedelta(seconds=60)
SETTINGS_INTERVAL_STANDBY = timedelta(minutes=15)


def settings_supported(device: EcamDevice) -> bool:
    return bool(device.profile.settings)


class SettingsCoordinator(DataUpdateCoordinator[dict[str, int | None]]):
    def __init__(self, hass: HomeAssistant, device: EcamDevice) -> None:
        super().__init__(
            hass, _LOGGER, name=f"{DOMAIN}_settings", update_interval=SETTINGS_INTERVAL_STANDBY
        )
        self.device = device
        # One settings operation at a time: a poll that read the old value must
        # not publish after a confirmed write (Codex review A6).
        self._lock = asyncio.Lock()
        self._was_awake = False

    def _awake(self) -> bool:
        state = self.device.state
        return state is not None and state.status != 0

    def _adjust_interval(self) -> None:
        self.update_interval = SETTINGS_INTERVAL_AWAKE if self._awake() else SETTINGS_INTERVAL_STANDBY

    def follow(self, status: DataUpdateCoordinator) -> Callable[[], None]:
        """Switch to the fast interval as soon as the machine wakes up.

        Without this the next standby poll could be up to 15 minutes away
        (Codex review A7). Returns the listener remover for unload.
        """

        @callback
        def _status_changed() -> None:
            awake = self._awake()
            if awake and not self._was_awake:
                self.update_interval = SETTINGS_INTERVAL_AWAKE
                self.hass.async_create_task(self.async_request_refresh())
            self._was_awake = awake

        return status.async_add_listener(_status_changed)

    async def _async_update_data(self) -> dict[str, int | None]:
        async with self._lock:
            return await self._read_all()

    async def _read_all(self) -> dict[str, int | None]:
        self._adjust_interval()
        values: dict[str, int | None] = dict(self.data or {})
        errors = []
        for key in self.device.profile.settings:
            try:
                values[key] = await self.device.read_setting(key)
            except EcamError as err:
                # Keep the last known value: a busy machine may skip one read.
                errors.append(f"{key}: {err}")
                values.setdefault(key, None)
        if errors and all(value is None for value in values.values()):
            raise UpdateFailed("; ".join(errors))
        if errors:
            _LOGGER.debug("some settings could not be read: %s", "; ".join(errors))
        return values

    async def async_write(self, key: str, value: int) -> None:
        """Write a setting; aioecam confirms it by reading it back."""
        async with self._lock:
            await self.device.write_setting(key, value)
            self.async_set_updated_data({**(self.data or {}), key: value})
