"""Migration of v1 entries (cremalink + add-on) to v2 (aioecam, no add-on)."""

from __future__ import annotations

import json
from pathlib import Path

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cremalink_ha.const import DOMAIN
from tests.conftest import DSN, IP, LAN_KEY
from tests.fakes.fake_machine import FakeMachine

# What a v0.2 entry from the add-on era looks like.
V1_LOCAL = {
    "connection_type": "local",
    "addon_url": "http://localhost:10280",
    "dsn": DSN,
    "lan_key": LAN_KEY,
    "device_ip": IP,
    "device_map": "ECAM610",
}


def _v1_entry(hass: HomeAssistant, data: dict) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, version=1, title="PrimaDonna Soul", data=data,
                            unique_id=DSN)  # fmt: skip
    entry.add_to_hass(hass)
    return entry


def _register_v1_entities(hass: HomeAssistant, entry: MockConfigEntry) -> dict[str, str]:
    """Entities as they exist in an installation today, with user-chosen entity ids."""
    registry = er.async_get(hass)
    ids = {}
    for domain, suffix, object_id in [
        ("sensor", "status_name", "kaffee_status"),
        ("switch", "power", "kaffee_power"),
        ("button", "cmd_espresso", "kaffee_espresso"),
        ("sensor", "statistics_total_beverages", "kaffee_getraenke_gesamt"),
        ("binary_sensor", "is_watertank_empty", "kaffee_wassertank_leer"),
    ]:
        registry.async_get_or_create(
            domain, DOMAIN, f"{entry.entry_id}_{suffix}", config_entry=entry,
            suggested_object_id=object_id,
        )  # fmt: skip
        ids[suffix] = f"{domain}.{object_id}"
    return ids


async def test_local_entry_migrates_and_keeps_entities(
    hass: HomeAssistant, machine: FakeMachine, tmp_path: Path
) -> None:
    token_file = tmp_path / "token.json"
    token_file.write_text(json.dumps({"refresh_token": "legacy-refresh"}))
    entry = _v1_entry(hass, {**V1_LOCAL, "token_file": str(token_file)})
    ids = _register_v1_entities(hass, entry)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.version == 2
    assert entry.state is ConfigEntryState.LOADED
    assert entry.data == {
        "dsn": DSN,
        "device_ip": IP,
        "lan_key": LAN_KEY,
        "profile": "ecam61x",
        "refresh_token": "legacy-refresh",  # carried over for the cloud wake fallback
    }
    registry = er.async_get(hass)
    # The Soul cannot report "water tank empty": the old entity is cleaned up.
    assert registry.async_get(ids.pop("is_watertank_empty")) is None
    for suffix, entity_id in ids.items():
        reg = registry.async_get(entity_id)
        assert reg is not None, entity_id  # same entity id, so history and dashboards stay
        assert reg.unique_id == f"{entry.entry_id}_{suffix}"
    # The entities are live again on the new transport.
    assert hass.states.get(ids["status_name"]).state == "in_standby"
    assert hass.states.get(ids["power"]).state == "off"


async def test_unknown_device_map_falls_back_to_soul_profile(
    hass: HomeAssistant, machine: FakeMachine
) -> None:
    entry = _v1_entry(hass, {**V1_LOCAL, "device_map": "custom:my_map.json"})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.version == 2 and entry.data["profile"] == "ecam61x"


async def test_cloud_entry_migrates_and_asks_for_login(hass: HomeAssistant) -> None:
    entry = _v1_entry(hass, {"connection_type": "cloud", "dsn": DSN, "device_map": "ECAM610",
                             "token_file": "/nonexistent.json"})  # fmt: skip
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.version == 2
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["context"]["source"] for f in flows] == [SOURCE_REAUTH]


async def test_newer_entry_is_not_touched(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, version=3, data={"dsn": DSN}, unique_id=DSN)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.MIGRATION_ERROR
