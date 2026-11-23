"""Writable machine settings (auto-off, water hardness).

Settings are read with ``0x95``. Every read is a property write to the Wi-Fi
module, and each one makes the module open a TLS connection to the Ayla cloud
(#36). Settings only change at the display while the machine is awake, so in
standby they are read once after the machine was awake and then left alone.
Writes go through aioecam, which announces the write session and confirms
each write by reading it back.
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
# follow them closely then. In standby the interval only decides how soon the
# read after waking up happens; nothing is read while nothing can change.
# While the machine is busy (rinsing, brewing, cleaning) settings cannot be
# changed either, and the machine often drops the request (several "no
# answer" timeouts a day), so those rounds are skipped too.
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
        # Counts the status updates that showed the machine awake. A standby
        # read is skipped while nothing was seen awake since the last complete
        # read; a wake during a read keeps the next one due.
        self._awake_seen = 0
        self._read_at = -1  # _awake_seen when the last complete standby read began
        self.standby_reads_skipped = 0
        self.busy_reads_skipped = 0

    def _awake(self) -> bool:
        state = self.device.state
        return state is not None and state.status != 0

    def _standby(self) -> bool:
        state = self.device.state
        return state is not None and state.status == 0

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
            if awake:
                self._awake_seen += 1
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
        standby = self._standby()
        began = self._awake_seen
        if standby and self.data is not None and self._read_at == began:
            self.standby_reads_skipped += 1
            return self.data
        if self.data is not None and self.device.is_busy():
            self.busy_reads_skipped += 1
            return self.data
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
        elif standby:
            self._read_at = began
        return values

    async def async_write(self, key: str, value: int) -> None:
        """Write a setting; aioecam confirms it by reading it back."""
        async with self._lock:
            # A write that reaches the machine but fails to confirm must not
            # leave a stale value cached until the next wake-up.
            self._read_at = -1
            await self.device.write_setting(key, value)
            self.async_set_updated_data({**(self.data or {}), key: value})
