"""Settings selects, the profile select and the custom-amount brew service."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cremalink_ha.const import DOMAIN
from custom_components.cremalink_ha.services import custom_beverages
from tests.conftest import V2_DATA
from tests.fakes.fake_machine import FakeMachine

# lodzen's Soul captures (anonymised names), same as in aioecam's tests
D034_PROFILES_1_3 = (
    "0Eek8AEDAEEAbABpAGMAZQAAAAAAAAAAAAAQAEIAbwBiAAAAAAAAAAAAAAAAAAAF"
    "AFAAcgBvAGYAaQBsACAAMwAAAAAJAKhz"
)
D039_RECIPE_ESPRESSO_P1 = "0BSm8AEBCAABACgbAQIEBAAZAaPq"


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, version=2, title="Soul", data=V2_DATA, unique_id=V2_DATA["dsn"])
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _select(hass: HomeAssistant, entry: MockConfigEntry, suffix: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id("select", DOMAIN, f"{entry.entry_id}_{suffix}")
    assert entity_id, suffix
    return entity_id


async def _eventually(check: Callable[[], bool], timeout: float = 5.0) -> None:
    async with asyncio.timeout(timeout):
        while not check():
            await asyncio.sleep(0.05)


async def _power_on(hass: HomeAssistant, entry: MockConfigEntry, machine: FakeMachine) -> None:
    switch = er.async_get(hass).async_get_entity_id("switch", DOMAIN, f"{entry.entry_id}_power")
    await hass.services.async_call("switch", "turn_on", {"entity_id": switch}, blocking=True)
    await _eventually(lambda: machine.status == 7)
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()


async def test_setting_selects_read_and_write(hass: HomeAssistant, machine: FakeMachine) -> None:
    entry = await _setup(hass)
    auto_off = _select(hass, entry, "setting_auto_off")
    hardness = _select(hass, entry, "setting_water_hardness")
    await _eventually(lambda: hass.states.get(auto_off).state == "1_h")
    assert hass.states.get(hardness).state == "level_2"
    # settings are writable in standby; aioecam announces the session and reads back
    await hass.services.async_call(
        "select", "select_option", {"entity_id": auto_off, "option": "30_min"}, blocking=True
    )
    assert machine.settings[0x3E] == 1
    assert hass.states.get(auto_off).state == "30_min"


async def test_setting_select_unconfirmed_value_is_unknown(
    hass: HomeAssistant, machine: FakeMachine
) -> None:
    machine.settings[0x3E] = 3  # "3 h" or "2 h" depending on the source: not offered
    entry = await _setup(hass)
    auto_off = _select(hass, entry, "setting_auto_off")
    await _eventually(lambda: hass.states.get(auto_off).attributes.get("raw_value") == 3)
    state = hass.states.get(auto_off)
    assert state.state == "unknown"
    assert "3_h" not in state.attributes["options"]


async def test_profile_select_needs_awake_machine(hass: HomeAssistant, machine: FakeMachine) -> None:
    machine.profile_names_value = D034_PROFILES_1_3
    entry = await _setup(hass)
    profile = _select(hass, entry, "profile")
    assert hass.states.get(profile).state == "unknown"
    with pytest.raises(HomeAssistantError, match="Switch the machine on"):
        await hass.services.async_call(
            "select", "select_option", {"entity_id": profile, "option": "2"}, blocking=True
        )
    assert machine.profile == 1 and not any(f.opcode == 0xA9 for f in machine.received)

    await _power_on(hass, entry, machine)
    # names are only answered while awake, so they appear after waking
    await _eventually(lambda: "2: Bob" in hass.states.get(profile).attributes["options"])
    await hass.services.async_call(
        "select", "select_option", {"entity_id": profile, "option": "2: Bob"}, blocking=True
    )
    assert machine.profile == 2
    state = hass.states.get(profile)
    assert state.state == "2: Bob" and state.attributes["slot"] == 2


def _device_id(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    (device,) = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    return device.id


async def test_brew_custom_sends_only_the_changed_amount(
    hass: HomeAssistant, machine: FakeMachine
) -> None:
    machine.recipes[(1, 1)] = D039_RECIPE_ESPRESSO_P1
    entry = await _setup(hass)
    await _power_on(hass, entry, machine)
    machine.step = 1.5  # a brew must outlast aioecam's 3 s confirmation poll
    await hass.services.async_call(
        DOMAIN,
        "brew_custom",
        {"device_id": _device_id(hass, entry), "beverage": "espresso", "coffee_ml": 30},
        blocking=True,
    )
    sent = [f for f in machine.received if f.opcode == 0x83][-1]
    # coffee 30 ml (0x001e), everything else as the machine's own recipe
    assert sent.payload.hex() == "0101" + "01001e" + "1b01" + "0204" + "0400" + "06"


async def test_brew_custom_refuses_invalid_requests(hass: HomeAssistant, machine: FakeMachine) -> None:
    machine.recipes[(1, 1)] = D039_RECIPE_ESPRESSO_P1
    entry = await _setup(hass)
    device_id = _device_id(hass, entry)

    with pytest.raises(ServiceValidationError, match="cannot be brewed"):
        await hass.services.async_call(
            DOMAIN, "brew_custom", {"device_id": device_id, "beverage": "doppio_plus"}, blocking=True
        )
    with pytest.raises(ServiceValidationError, match="no parameter"):
        await hass.services.async_call(
            DOMAIN, "brew_custom", {"device_id": device_id, "beverage": "espresso", "milk": 100},
            blocking=True,
        )  # fmt: skip
    with pytest.raises(ServiceValidationError, match="not a CremaEvolution"):
        await hass.services.async_call(
            DOMAIN, "brew_custom", {"device_id": "nope", "beverage": "espresso"}, blocking=True
        )
    assert not any(f.opcode == 0x83 for f in machine.received)


async def test_custom_beverages_excludes_unknown_start_byte(
    hass: HomeAssistant, machine: FakeMachine
) -> None:
    entry = await _setup(hass)
    names = custom_beverages(entry.runtime_data.device.profile)
    assert "espresso" in names and "hot_water" in names
    assert "doppio_plus" not in names


async def test_brew_custom_has_no_temperature_field(hass: HomeAssistant, machine: FakeMachine) -> None:
    # Tag 0x1B is the app's INDEX_LENGTH, not a temperature (Coffee Link 4.9.6).
    import voluptuous as vol

    machine.recipes[(1, 1)] = D039_RECIPE_ESPRESSO_P1
    entry = await _setup(hass)
    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN,
            "brew_custom",
            {"device_id": _device_id(hass, entry), "beverage": "espresso", "temperature": 2},
            blocking=True,
        )
    assert not any(f.opcode == 0x83 for f in machine.received)



async def test_brew_custom_refuses_pre_ground_intensity(hass: HomeAssistant, machine: FakeMachine) -> None:
    # TASTE 0 means pre-ground coffee in the app; milk is tenths of a second
    import voluptuous as vol

    machine.recipes[(1, 1)] = D039_RECIPE_ESPRESSO_P1
    entry = await _setup(hass)
    for extra in ({"intensity": 0}, {"milk": 2000}):
        with pytest.raises(vol.Invalid):
            await hass.services.async_call(
                DOMAIN,
                "brew_custom",
                {"device_id": _device_id(hass, entry), "beverage": "espresso", **extra},
                blocking=True,
            )
    assert not any(f.opcode == 0x83 for f in machine.received)
