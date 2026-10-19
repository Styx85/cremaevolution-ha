"""Writable machine settings (auto-off, water hardness).

Settings are read with ``0x95`` and work in standby too, so they are polled
slowly and independently of the machine state. Writes go through aioecam,
which announces the write session and confirms each write by reading it back.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from aioecam.device import EcamDevice
from aioecam.exceptions import EcamError
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

SETTINGS_UPDATE_INTERVAL = timedelta(minutes=15)


def settings_supported(device: EcamDevice) -> bool:
    return bool(device.profile.settings)


class SettingsCoordinator(DataUpdateCoordinator[dict[str, int | None]]):
    def __init__(self, hass: HomeAssistant, device: EcamDevice) -> None:
        super().__init__(
            hass, _LOGGER, name=f"{DOMAIN}_settings", update_interval=SETTINGS_UPDATE_INTERVAL
        )
        self.device = device

    async def _async_update_data(self) -> dict[str, int | None]:
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
        await self.device.write_setting(key, value)
        self.async_set_updated_data({**(self.data or {}), key: value})
