"""Machines without LAN mode run through the De'Longhi cloud (issue #27)."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any

import pytest
from aioecam.cloud.link import CloudLink
from aioecam.ecam import commands
from aioecam.ecam.value import decode_value
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.cremalink_ha as integration
from custom_components.cremalink_ha.const import DOMAIN
from tests.conftest import DSN, V2_DATA
from tests.fakes.fake_cloud import MemoryCloud
from tests.fakes.fake_machine import FakeMachine

CLOUD_DATA = {"dsn": DSN, "device_ip": None, "lan_key": None, "profile": "ecam61x",
              "refresh_token": "refresh-0"}  # fmt: skip


@pytest.fixture
def cloud(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[MemoryCloud, FakeMachine]]:
    memory = MemoryCloud()
    sim = FakeMachine(memory)  # type: ignore[arg-type]
    sim.step = 0.1
    monkeypatch.setattr(integration, "AylaCloud", lambda *a, **k: memory)
    monkeypatch.setattr(integration, "CLOUD_POLL_INTERVAL", 0.3)
    monkeypatch.setattr(integration, "CLOUD_FAST_INTERVAL", 0.05)
    yield memory, sim


async def _setup(hass: HomeAssistant, data: dict[str, Any], options: dict[str, Any] | None = None) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, version=2, title="Eletta", data=data,
                            options=options or {}, unique_id=DSN)  # fmt: skip
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _entity(hass: HomeAssistant, entry: MockConfigEntry, domain: str, key: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(domain, DOMAIN, f"{entry.entry_id}_{key}")
    assert entity_id, key
    return entity_id


async def test_entry_without_lan_key_runs_through_the_cloud(
    hass: HomeAssistant, cloud: tuple[MemoryCloud, FakeMachine]
) -> None:
    memory, _ = cloud
    entry = await _setup(hass, CLOUD_DATA)
    assert entry.state is ConfigEntryState.LOADED
    runtime = entry.runtime_data
    assert isinstance(runtime.link, CloudLink)
    assert "lan" not in hass.data.get(DOMAIN, {})  # no LAN callback server
    assert hass.states.get(_entity(hass, entry, "sensor", "status_name")).state == "in_standby"
    # Features that cost many cloud requests are left out.
    assert runtime.statistics_coordinator is None and runtime.settings_coordinator is None
    assert runtime.recipe_reader is None
    # The keep-alive went out at setup: for the Soul profile a monitor request.
    name, value = memory.writes[0]
    assert name == "data_request" and decode_value(value).frame == commands.monitor_request()


async def test_power_switch_through_the_cloud(
    hass: HomeAssistant, cloud: tuple[MemoryCloud, FakeMachine]
) -> None:
    _, sim = cloud
    entry = await _setup(hass, CLOUD_DATA)
    switch = _entity(hass, entry, "switch", "power")
    await hass.services.async_call("switch", "turn_on", {"entity_id": switch}, blocking=True)
    await hass.async_block_till_done()
    assert sim.status in (1, 7) and hass.states.get(switch).state == "on"
    await hass.services.async_call("switch", "turn_off", {"entity_id": switch}, blocking=True)
    await hass.async_block_till_done()
    assert sim.status in (0, 2)
    # "Going to sleep" still counts as on; the cloud poll brings the final standby.
    for _ in range(40):
        await asyncio.sleep(0.05)
        await hass.async_block_till_done()
        if hass.states.get(switch).state == "off":
            break
    assert hass.states.get(switch).state == "off"
    assert sim.monitor_requests <= 1  # only the keep-alive, no per-poll requests


async def test_cloud_option_overrides_a_lan_key(
    hass: HomeAssistant, cloud: tuple[MemoryCloud, FakeMachine]
) -> None:
    entry = await _setup(hass, {**V2_DATA, "refresh_token": "refresh-0"}, {"connection": "cloud"})
    assert isinstance(entry.runtime_data.link, CloudLink)


async def test_unload_closes_the_cloud_link(
    hass: HomeAssistant, cloud: tuple[MemoryCloud, FakeMachine]
) -> None:
    entry = await _setup(hass, CLOUD_DATA)
    link = entry.runtime_data.link
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert link.state == "closed"


async def test_cloud_entry_without_sign_in_asks_for_login(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, version=2, unique_id=DSN,
                            data={**CLOUD_DATA, "refresh_token": None})  # fmt: skip
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR


async def test_rejected_sign_in_asks_for_login(
    hass: HomeAssistant, cloud: tuple[MemoryCloud, FakeMachine], monkeypatch: pytest.MonkeyPatch
) -> None:
    from aioecam.exceptions import AuthError

    memory, _ = cloud
    entry = await _setup(hass, CLOUD_DATA)

    async def rejected(dsn: str, names: list[str] | None) -> dict[str, Any]:
        raise AuthError("cloud rejected a fresh access token")

    monkeypatch.setattr(memory, "read_properties", rejected)
    link = entry.runtime_data.link
    link._wake.set()
    for _ in range(40):
        if link.stats.auth_failed:
            break
        await asyncio.sleep(0.05)
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["context"]["source"] for f in flows] == ["reauth"]


async def test_local_only_without_key_is_an_error_not_a_login_loop(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, version=2, unique_id=DSN, data=CLOUD_DATA,
                            options={"connection": "lan"})  # fmt: skip
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)


async def test_key_without_address_uses_the_cloud(
    hass: HomeAssistant, cloud: tuple[MemoryCloud, FakeMachine]
) -> None:
    entry = await _setup(hass, {**V2_DATA, "device_ip": None, "refresh_token": "refresh-0"})
    assert isinstance(entry.runtime_data.link, CloudLink)
