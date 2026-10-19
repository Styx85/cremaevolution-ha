"""Service: brew a stored beverage with custom amounts.

The machine's own recipe for the chosen profile is read first; only the
amounts passed to the service are changed. Parameters the recipe does not
have are refused instead of invented (aioecam ``brew_custom``).
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from aioecam.device import (
    TAG_COFFEE_ML,
    TAG_INTENSITY,
    TAG_MILK,
    TAG_WATER_ML,
)
from aioecam.ecam.commands import OPCODE_BREW, BrewAction
from aioecam.exceptions import EcamError
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN

SERVICE_BREW_CUSTOM = "brew_custom"
ATTR_DEVICE_ID = "device_id"
ATTR_BEVERAGE = "beverage"
ATTR_PROFILE = "profile"

# Service field -> recipe tag, with conservative bounds. Intensity is the
# app's raw TASTE step (0-5). There is deliberately no temperature field: tag
# 0x1B, once thought to be temperature, is the app's INDEX_LENGTH.
AMOUNTS: dict[str, tuple[int, int, int]] = {
    "coffee_ml": (TAG_COFFEE_ML, 15, 250),
    "water_ml": (TAG_WATER_ML, 20, 420),  # hot water/tea descriptors on the Soul
    "milk": (TAG_MILK, 0, 1000),
    "intensity": (TAG_INTENSITY, 0, 5),
}

BREW_CUSTOM_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Required(ATTR_BEVERAGE): cv.string,
        vol.Optional(ATTR_PROFILE, default=1): vol.All(vol.Coerce(int), vol.Range(min=1, max=5)),
        **{
            vol.Optional(field): vol.All(vol.Coerce(int), vol.Range(min=low, max=high))
            for field, (_tag, low, high) in AMOUNTS.items()
        },
    }
)


def custom_beverages(profile: Any) -> list[str]:
    """Stored beverages whose frame is a plain start command.

    ``doppio_plus`` on the Soul stores ``02`` where the start/stop byte is
    (``0d0f83f005 02 …``); its meaning is unknown, so it is not rebuilt with a
    forced "start" here. Its normal button still sends the stored frame.
    """
    return sorted(
        name
        for name, cmd in profile.commands.items()
        if cmd.frame.opcode == OPCODE_BREW and cmd.frame.payload[1] == BrewAction.START
    )


def _runtime_for_device(hass: HomeAssistant, device_id: str) -> Any:
    device = dr.async_get(hass).async_get(device_id)
    if device is not None:
        for entry_id in device.config_entries:
            entry = hass.config_entries.async_get_entry(entry_id)
            if entry is not None and entry.domain == DOMAIN:
                if entry.state is not ConfigEntryState.LOADED:
                    raise HomeAssistantError(f"{entry.title} is not loaded")
                return entry.runtime_data
    raise ServiceValidationError(f"{device_id} is not a Cremalink coffee machine")


async def _async_brew_custom(call: ServiceCall) -> None:
    hass = call.hass
    runtime = _runtime_for_device(hass, call.data[ATTR_DEVICE_ID])
    ecam = runtime.device
    beverage = call.data[ATTR_BEVERAGE]
    choices = custom_beverages(ecam.profile)
    if beverage not in choices:
        raise ServiceValidationError(
            f"{beverage!r} cannot be brewed with custom amounts; choose one of {', '.join(choices)}"
        )
    overrides = {AMOUNTS[f][0]: call.data[f] for f in AMOUNTS if f in call.data}
    try:
        await ecam.brew_custom(beverage, overrides, slot=call.data[ATTR_PROFILE])
    except ValueError as err:
        # e.g. milk for a beverage without milk: refuse rather than invent a parameter
        raise ServiceValidationError(str(err)) from err
    except EcamError as err:
        raise HomeAssistantError(f"Could not start {beverage}: {err}") from err
    await runtime.coordinator.async_request_refresh()


def async_setup_services(hass: HomeAssistant) -> None:
    if not hass.services.has_service(DOMAIN, SERVICE_BREW_CUSTOM):
        hass.services.async_register(
            DOMAIN, SERVICE_BREW_CUSTOM, _async_brew_custom, schema=BREW_CUSTOM_SCHEMA
        )
