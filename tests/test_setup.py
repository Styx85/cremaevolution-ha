"""Setup, entities and commands against a simulated machine."""

from __future__ import annotations

import pytest
from homeassistant.config_entries import ConfigEntryState
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
