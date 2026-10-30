"""Selects for machine settings and the active user profile."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any

from aioecam.exceptions import EcamError
from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import CremalinkConfigEntry
from .const import DOMAIN, MANUFACTURER
from .coordinator import EcamCoordinator
from .settings_coordinator import SettingsCoordinator

_LOGGER = logging.getLogger(__name__)

# Raw value -> option key. Only confirmed values are offered (see
# docs/ecam-lan-protocol.md, 7.6). Auto-off 3 = 3 h: the app's Wi-Fi branch
# maps it so, and setting 3 h on an ECAM610.75's display read back as 3
# (2026-10-20). The app's non-Wi-Fi branch (3 = 2 h, 4 = 3 h) is not used by
# these machines; any other value shows as unknown with its raw value.
SETTING_OPTIONS: dict[str, dict[int, str]] = {
    "auto_off": {0: "15_min", 1: "30_min", 2: "1_h", 3: "3_h"},
    "water_hardness": {0: "level_1", 1: "level_2", 2: "level_3", 3: "level_4"},
}

# Profile names are only answered while the machine is awake; retry this often.
PROFILE_NAMES_RETRY = 600.0


def _device_info(entry: CremalinkConfigEntry) -> DeviceInfo:
    return DeviceInfo(identifiers={(DOMAIN, entry.entry_id)}, name=entry.title,
                      manufacturer=MANUFACTURER)  # fmt: skip


async def async_setup_entry(
    hass: HomeAssistant, entry: CremalinkConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    runtime = entry.runtime_data
    entities: list[SelectEntity] = []
    if runtime.settings_coordinator is not None:
        for key in runtime.device.profile.settings:
            if key in SETTING_OPTIONS:
                entities.append(CremalinkSettingSelect(runtime.settings_coordinator, entry, key))
    if runtime.device.profile.session == "device_connected":  # profile writes need it
        entities.append(CremalinkProfileSelect(runtime.coordinator, entry))
    async_add_entities(entities)


class CremalinkSettingSelect(CoordinatorEntity[SettingsCoordinator], SelectEntity):
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: SettingsCoordinator, entry: CremalinkConfigEntry, key: str):
        super().__init__(coordinator)
        self._key = key
        self._values = SETTING_OPTIONS[key]
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry.entry_id}_setting_{key}"
        self._attr_options = list(self._values.values())
        self._attr_device_info = _device_info(entry)

    @property
    def _raw(self) -> int | None:
        return (self.coordinator.data or {}).get(self._key)

    @property
    def available(self) -> bool:
        return super().available and self._raw is not None

    @property
    def current_option(self) -> str | None:
        raw = self._raw
        return None if raw is None else self._values.get(raw)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"raw_value": self._raw}

    async def async_select_option(self, option: str) -> None:
        raw = next(value for value, key in self._values.items() if key == option)
        try:
            await self.coordinator.async_write(self._key, raw)
        except (EcamError, ValueError, KeyError) as err:
            raise HomeAssistantError(f"Could not change {self._key}: {err}") from err


class CremalinkProfileSelect(CoordinatorEntity[EcamCoordinator], SelectEntity, RestoreEntity):
    """The active user profile.

    The machine does not report which profile is active; this entity shows the
    last profile selected through Home Assistant and acknowledged by the machine.
    Names are loaded once the machine is awake, because only then does it
    answer the name request.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "profile"

    def __init__(self, coordinator: EcamCoordinator, entry: CremalinkConfigEntry) -> None:
        super().__init__(coordinator)
        self._device = coordinator.device
        self._attr_unique_id = f"{entry.entry_id}_profile"
        self._attr_device_info = _device_info(entry)
        self._names: dict[int, str] = {}
        self._slot: int | None = None
        self._loading = False
        self._last_attempt = 0.0
        self._names_task: asyncio.Task[None] | None = None

    def _label(self, slot: int) -> str:
        name = self._names.get(slot)
        return f"{slot}: {name}" if name else str(slot)

    @property
    def options(self) -> list[str]:
        slots = sorted(self._names) or range(1, self._device.profile.profile_slots + 1)
        return [self._label(slot) for slot in slots]

    @property
    def current_option(self) -> str | None:
        return None if self._slot is None else self._label(self._slot)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"slot": self._slot, "source": "last selection confirmed by the machine"}

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) and last.state not in ("unknown", "unavailable"):
            try:
                self._slot = int(last.state.split(":", 1)[0])
            except ValueError:
                self._slot = None
        self._maybe_load_names()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._maybe_load_names()
        super()._handle_coordinator_update()

    def _awake(self) -> bool:
        view = self.coordinator.data
        return view is not None and view.state.status != 0

    @callback
    def _maybe_load_names(self) -> None:
        if self._names or self._loading or not self._awake():
            return
        if time.monotonic() - self._last_attempt < PROFILE_NAMES_RETRY and self._last_attempt:
            return
        self._loading = True
        self._last_attempt = time.monotonic()
        # Owned by this entity: cancelled on removal or reload (Codex review A8).
        self._names_task = self.hass.async_create_background_task(
            self._load_names(), "cremalink profile names"
        )

    async def async_will_remove_from_hass(self) -> None:
        if self._names_task is not None and not self._names_task.done():
            self._names_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._names_task
        self._names_task = None
        await super().async_will_remove_from_hass()

    async def _load_names(self) -> None:
        try:
            slots = await self._device.profiles(timeout=10)
        except EcamError as err:
            _LOGGER.debug("profile names not available yet: %s", err)
        else:
            self._names = {slot.slot: slot.name for slot in slots}
            if self.hass is not None and self.entity_id is not None:
                self.async_write_ha_state()
        finally:
            self._loading = False

    async def async_select_option(self, option: str) -> None:
        slot = int(option.split(":", 1)[0])
        if not self._awake():
            # Selecting a profile in standby has not been verified on hardware.
            raise HomeAssistantError("Switch the machine on before changing the profile")
        try:
            await self._device.select_profile(slot)
        except (EcamError, ValueError) as err:
            raise HomeAssistantError(f"Could not select profile {slot}: {err}") from err
        self._slot = slot
        self.async_write_ha_state()
