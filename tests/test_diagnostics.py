from __future__ import annotations

import json

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cremalink_ha.const import DOMAIN
from custom_components.cremalink_ha.diagnostics import async_get_config_entry_diagnostics
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
