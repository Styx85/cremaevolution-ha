from __future__ import annotations

import json
from unittest.mock import patch

from custom_components.cremalink_ha.const import DOMAIN
from custom_components.cremalink_ha.diagnostics import (
    async_get_config_entry_diagnostics,
)
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from tests.conftest import LAN_KEY, V2_DATA
from tests.fakes.fake_machine import FakeMachine


async def test_diagnostics_redact_secrets(hass: HomeAssistant, machine: FakeMachine) -> None:
    data = {**V2_DATA, "refresh_token": "refresh-secret"}
    entry = MockConfigEntry(domain=DOMAIN, version=2, title="Soul", data=data, unique_id=data["dsn"])
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    diag = await async_get_config_entry_diagnostics(hass, entry)
    dumped = json.dumps(diag, default=str)
    assert LAN_KEY not in dumped and "refresh-secret" not in dumped and "127.0.0.1" not in dumped
    assert diag["profile"] == "ecam61x"
    assert diag["link"]["state"] == "active" and diag["link"]["key_exchanges"] >= 1
    assert diag["monitor"]["status_name"] == "in_standby"
    assert diag["cloud_fallback_configured"] is True


async def test_passive_catalog_and_freshness_export(hass: HomeAssistant, machine: FakeMachine) -> None:
    from aioecam.ecam.frame import RESPONSE, Frame
    from aioecam.ecam.value import encode_value

    entry = MockConfigEntry(domain=DOMAIN, version=2, title="Soul", data=V2_DATA,
                            unique_id=V2_DATA["dsn"])
    entry.add_to_hass(hass)
    with patch("custom_components.cremalink_ha.statistics_supported", return_value=False):
        assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    runtime = entry.runtime_data
    count = len(machine.received)
    await machine.device.push("recipe", encode_value(
        Frame(RESPONSE, 0xA6, 0xF0, bytes.fromhex("01 01 01 0028"))))
    await machine.device.push("names", encode_value(
        Frame(RESPONSE, 0xA4, 0xF0, b"\x01\x03" + "Private Person".encode("utf-16-be"))))
    await machine.device.push("software_version", "ADA test")
    first = await async_get_config_entry_diagnostics(hass, entry)
    await runtime.device.refresh()
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["freshness"]["monitor_query_result"] == "response_received"
    assert diag["freshness"]["last_valid_monitor_received_at"] >= first["freshness"]["last_valid_monitor_received_at"]
    assert diag["identity"]["oem_model"] is None
    assert diag["identity"]["observed_machine_properties"]["software_version"] == "ADA test"
    assert diag["recipe_catalog"]["property_count"] == 2
    assert diag["recipe_catalog"]["entries"][0]["data"]["parameters"] == {1: 40}
    dumped = json.dumps(diag, default=str)
    assert "Private Person" not in dumped and V2_DATA["dsn"] not in dumped
    assert len(machine.received) == count  # passive parse + monitor GET, no ECAM command
