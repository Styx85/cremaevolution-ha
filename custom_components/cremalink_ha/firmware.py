"""Show the Wi-Fi module's firmware string as the device's firmware in HA.

The module reports it in the ``software_version`` property (on an ECAM610.75:
"Millcore_demo 2.0.0 Jun 18 2020"). It is read once per setup with a plain
property GET, which also works in standby; Home Assistant keeps the value in
its device registry across restarts.
"""

from __future__ import annotations

import logging

from aioecam.device import EcamDevice
from aioecam.exceptions import EcamError
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN, MANUFACTURER

_LOGGER = logging.getLogger(__name__)

FIRMWARE_PROPERTY = "software_version"


async def async_update_firmware(hass: HomeAssistant, entry: ConfigEntry, device: EcamDevice) -> None:
    try:
        point = await device.link.read(FIRMWARE_PROPERTY, timeout=10)
    except (EcamError, TimeoutError) as err:
        _LOGGER.debug("firmware version not available: %s", err)
        return
    value = point.value.strip()[:64] if isinstance(point.value, str) else ""
    if not value:
        return
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer=MANUFACTURER,
        sw_version=value,
    )
