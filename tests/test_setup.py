"""Setup, entities and commands against a simulated machine."""

from __future__ import annotations

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cremalink_ha.const import DOMAIN
from tests.conftest import V2_DATA
from tests.fakes.fake_machine import FakeMachine


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, version=2, title="Soul", data=V2_DATA, unique_id=V2_DATA["dsn"])
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _entity(hass: HomeAssistant, entry: MockConfigEntry, unique_suffix: str) -> str:
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        unique_suffix.split(":")[0], DOMAIN, f"{entry.entry_id}_{unique_suffix.split(':')[1]}"
    )
    assert entity_id, unique_suffix
    return entity_id


async def test_setup_creates_entities(hass: HomeAssistant, machine: FakeMachine) -> None:
    entry = await _setup(hass)
    assert entry.state is ConfigEntryState.LOADED

    status = hass.states.get(_entity(hass, entry, "sensor:status_name"))
    assert status is not None and status.state == "in_standby"
    assert hass.states.get(_entity(hass, entry, "switch:power")).state == "off"
    assert hass.states.get(_entity(hass, entry, "button:cmd_espresso")) is not None

    registry = er.async_get(hass)
    binary = {e.unique_id.removeprefix(f"{entry.entry_id}_")
              for e in er.async_entries_for_config_entry(registry, entry.entry_id)
              if e.domain == "binary_sensor"}  # fmt: skip
    assert "is_busy" in binary and "is_waste_container_full" in binary
    # The Soul has no trustworthy "water tank empty" bit, so no entity for it.
    assert "is_watertank_empty" not in binary


async def test_power_switch_wakes_and_sleeps(hass: HomeAssistant, machine: FakeMachine) -> None:
    entry = await _setup(hass)
    switch = _entity(hass, entry, "switch:power")
    await hass.services.async_call("switch", "turn_on", {"entity_id": switch}, blocking=True)
    assert machine.status in (1, 7)
    await hass.async_block_till_done()
    assert hass.states.get(switch).state == "on"
    await hass.services.async_call("switch", "turn_off", {"entity_id": switch}, blocking=True)
    assert machine.status in (0, 2)


async def test_brew_refused_while_off(hass: HomeAssistant, machine: FakeMachine) -> None:
    entry = await _setup(hass)
    button = _entity(hass, entry, "button:cmd_espresso")
    with pytest.raises(HomeAssistantError, match="not ready"):
        await hass.services.async_call("button", "press", {"entity_id": button}, blocking=True)
    assert not [f for f in machine.received if f.opcode == 0x83]


async def test_statistics_read_locally(hass: HomeAssistant, machine: FakeMachine) -> None:
    entry = await _setup(hass)
    stats = entry.runtime_data.statistics_coordinator
    assert stats is not None
    await stats.async_refresh()
    await hass.async_block_till_done()
    assert stats.data["known"]["descale_count"] == 30
    sensor = hass.states.get(_entity(hass, entry, "sensor:statistics_total_beverages"))
    assert sensor is not None and sensor.state == str(396 + 5231 + 2 + 180)


async def test_raw_descale_sensor_is_opt_in_and_keeps_user_choice(
    hass: HomeAssistant, machine: FakeMachine
) -> None:
    entry = await _setup(hass)
    registry = er.async_get(hass)
    raw_id = _entity(hass, entry, "sensor:statistics_descale_load_raw")
    raw = registry.async_get(raw_id)
    assert raw is not None
    assert raw.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert raw.entity_category is EntityCategory.DIAGNOSTIC
    assert hass.states.get(raw_id) is None

    # Ordinary maintenance counters remain enabled.
    count_id = _entity(hass, entry, "sensor:statistics_descale_count")
    assert registry.async_get(count_id).disabled_by is None

    # An explicit user choice survives a reload even though the default is off.
    registry.async_update_entity(raw_id, disabled_by=None)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    await entry.runtime_data.statistics_coordinator.async_refresh()
    await hass.async_block_till_done()
    assert registry.async_get(raw_id).disabled_by is None
    state = hass.states.get(raw_id)
    assert state is not None and state.state == "981155"


async def test_hot_water_updates_and_retires_incorrect_tea_entity(
    hass: HomeAssistant, machine: FakeMachine
) -> None:
    machine.statistics.update({3018: 0, 3019: 2})
    entry = MockConfigEntry(domain=DOMAIN, version=2, title="Soul", data=V2_DATA,
                            unique_id=V2_DATA["dsn"])  # fmt: skip
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    old_tea = registry.async_get_or_create(
        "sensor", DOMAIN, f"{entry.entry_id}_statistics_tea", suggested_object_id="old_tea"
    )
    existing_water = registry.async_get_or_create(
        "sensor", DOMAIN, f"{entry.entry_id}_statistics_hot_water",
        suggested_object_id="existing_hot_water",
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    stats = entry.runtime_data.statistics_coordinator
    await stats.async_refresh()
    await hass.async_block_till_done()
    water_id = _entity(hass, entry, "sensor:statistics_hot_water")
    assert water_id == existing_water.entity_id
    assert registry.async_get(old_tea.entity_id) is None
    assert registry.async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}_statistics_tea"
    ) is None
    assert hass.states.get(water_id).state == "2"

    # Reproduce the operator's hot-water observation; A2 ID 3018 stays zero.
    machine.statistics[3019] = 3
    await stats.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(water_id).state == "3"
    assert stats.data["unknown"][3018] == 0


async def test_unreachable_machine_retries_later(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, version=2, data=V2_DATA, unique_id=V2_DATA["dsn"])
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_unload_releases_the_server(hass: HomeAssistant, machine: FakeMachine) -> None:
    entry = await _setup(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert "lan" not in hass.data.get(DOMAIN, {})


async def test_token_rotation_does_not_reload(hass: HomeAssistant, machine: FakeMachine) -> None:
    """Regression: a rotated refresh token was saved with a reload listener in place,
    so every cloud call restarted the integration in a loop (entities unavailable)."""
    entry = MockConfigEntry(domain=DOMAIN, version=2, title="Soul", unique_id=V2_DATA["dsn"],
                            data={**V2_DATA, "refresh_token": "refresh-0"})  # fmt: skip
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    runtime = entry.runtime_data
    assert runtime.cloud is not None

    # Exactly what aioecam does after a token refresh.
    runtime.cloud._on_refresh_token("refresh-1")  # type: ignore[misc]
    await hass.async_block_till_done()

    assert entry.data["refresh_token"] == "refresh-1"
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data is runtime  # same objects: not reloaded
    assert runtime.link.state == "active"


async def test_options_change_reloads(hass: HomeAssistant, machine: FakeMachine) -> None:
    entry = await _setup(hass)
    before = entry.runtime_data
    result = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.config_entries.options.async_configure(result["flow_id"], {"lan_port": 10281})
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data is not before  # reloaded with the new options
