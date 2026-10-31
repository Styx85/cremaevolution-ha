"""Config, reauth, reconfigure and options flows."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from aioecam.cloud.api import CloudDevice
from aioecam.cloud.onboarding import OnboardedMachine, Onboarding
from aioecam.discovery import ModuleInfo
from aioecam.exceptions import AuthError
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cremalink_ha.const import DOMAIN
from tests.conftest import DSN, LAN_KEY, V2_DATA

FLOW = "custom_components.cremalink_ha.config_flow"
LOGIN = {"email": "user@example.com", "password": "secret"}


def _machine(dsn: str = DSN, *, lan_key: str | None = LAN_KEY, name: str = "Soul") -> OnboardedMachine:
    device = CloudDevice(dsn=dsn, name=name, oem_model="DL-millcore", lan_enabled=lan_key is not None,
                         lan_ip="192.168.1.50", connection_status="Online")  # fmt: skip
    return OnboardedMachine(device=device, profile_family="ecam61x", lan_key=lan_key, key_id=58915)


def _onboarding(*machines: OnboardedMachine) -> AsyncMock:
    return AsyncMock(return_value=Onboarding(refresh_token="refresh-1", machines=list(machines)))


async def _start(hass: HomeAssistant) -> dict[str, Any]:
    return await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})


async def test_login_single_machine_creates_entry(hass: HomeAssistant) -> None:
    with (
        patch(f"{FLOW}.onboard", _onboarding(_machine())),
        patch("custom_components.cremalink_ha.async_setup_entry", return_value=True),
    ):
        result = await _start(hass)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], LOGIN)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Soul"
    assert result["data"] == {"dsn": DSN, "device_ip": "192.168.1.50", "lan_key": LAN_KEY,
                              "key_id": 58915, "profile": "ecam61x",
                              "refresh_token": "refresh-1", "oem_model": "DL-millcore"}  # fmt: skip
    assert "password" not in result["data"] and "email" not in result["data"]
    assert result["result"].unique_id == DSN


async def test_login_several_machines_asks(hass: HomeAssistant) -> None:
    other = _machine("AC000W000000009", name="Eletta")
    with (
        patch(f"{FLOW}.onboard", _onboarding(_machine(), other)),
        patch("custom_components.cremalink_ha.async_setup_entry", return_value=True),
    ):
        result = await _start(hass)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], LOGIN)
        assert result["step_id"] == "select"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"dsn": "AC000W000000009"}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY and result["title"] == "Eletta"


@pytest.mark.parametrize(
    ("side_effect", "error"),
    [(AuthError("rejected"), "invalid_auth"), (TimeoutError(), "cannot_connect")],
)
async def test_login_errors(hass: HomeAssistant, side_effect: Exception, error: str) -> None:
    with patch(f"{FLOW}.onboard", AsyncMock(side_effect=side_effect)):
        result = await _start(hass)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], LOGIN)
    assert result["type"] is FlowResultType.FORM and result["errors"] == {"base": error}


async def test_machine_without_lan_key_offers_the_cloud(hass: HomeAssistant) -> None:
    with (
        patch(f"{FLOW}.onboard", _onboarding(_machine(lan_key=None, name="Eletta"))),
        patch("custom_components.cremalink_ha.async_setup_entry", return_value=True),
    ):
        result = await _start(hass)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], LOGIN)
        assert result["type"] is FlowResultType.FORM and result["step_id"] == "cloud_confirm"
        assert result["description_placeholders"] == {"name": "Eletta"}
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["lan_key"] is None and result["data"]["refresh_token"] == "refresh-1"
    assert result["options"] == {"connection": "cloud"}


async def test_manual_setup(hass: HomeAssistant) -> None:
    module = ModuleInfo(dsn=DSN, registered=True, registration_type="AP-Mode")
    with (
        patch(f"{FLOW}.probe_module", AsyncMock(return_value=module)),
        patch("custom_components.cremalink_ha.async_setup_entry", return_value=True),
    ):
        result = await _start(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"manual_setup": True}
        )
        assert result["step_id"] == "manual"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"device_ip": " 192.168.1.50 ", "lan_key": LAN_KEY, "profile": "ecam61x"},
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {"dsn": DSN, "device_ip": "192.168.1.50", "lan_key": LAN_KEY,
                              "profile": "ecam61x"}  # fmt: skip


async def test_manual_setup_module_not_ready(hass: HomeAssistant) -> None:
    module = ModuleInfo(dsn=None, registered=True, registration_type=None)
    with patch(f"{FLOW}.probe_module", AsyncMock(return_value=module)):
        result = await _start(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"manual_setup": True}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"device_ip": "192.168.1.50", "lan_key": LAN_KEY, "profile": "ecam61x"}
        )
    assert result["errors"] == {"base": "module_not_ready"}


async def test_reauth_fills_in_lan_key(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, version=2, unique_id=DSN,
                            data={"dsn": DSN, "device_ip": None, "lan_key": None, "profile": "ecam61x"})  # fmt: skip
    entry.add_to_hass(hass)
    with (
        patch(f"{FLOW}.onboard", _onboarding(_machine())),
        patch("custom_components.cremalink_ha.async_setup_entry", return_value=True),
    ):
        result = await entry.start_reauth_flow(hass)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], LOGIN)
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reauth_successful"
    assert entry.data["lan_key"] == LAN_KEY and entry.data["device_ip"] == "192.168.1.50"
    assert entry.data["refresh_token"] == "refresh-1"


async def test_reauth_without_lan_key_continues_in_cloud_mode(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, version=2, unique_id=DSN,
                            data={"dsn": DSN, "device_ip": None, "lan_key": None, "profile": "ecam61x"})  # fmt: skip
    entry.add_to_hass(hass)
    with (
        patch(f"{FLOW}.onboard", _onboarding(_machine(lan_key=None))),
        patch("custom_components.cremalink_ha.async_setup_entry", return_value=True),
    ):
        result = await entry.start_reauth_flow(hass)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], LOGIN)
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reauth_successful"
    assert entry.data["lan_key"] is None and entry.data["refresh_token"] == "refresh-1"


async def test_reconfigure_ip(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, version=2, unique_id=DSN, data=V2_DATA)
    entry.add_to_hass(hass)
    module = ModuleInfo(dsn=DSN, registered=True, registration_type="AP-Mode")
    with (
        patch(f"{FLOW}.probe_module", AsyncMock(return_value=module)),
        patch("custom_components.cremalink_ha.async_setup_entry", return_value=True),
    ):
        result = await entry.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"device_ip": "192.168.1.77"}
        )
    assert result["reason"] == "reconfigure_successful"
    assert entry.data["device_ip"] == "192.168.1.77"


async def test_reconfigure_rejects_other_machine(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, version=2, unique_id=DSN, data=V2_DATA)
    entry.add_to_hass(hass)
    other = ModuleInfo(dsn="AC000W999999999", registered=True, registration_type="AP-Mode")
    with patch(f"{FLOW}.probe_module", AsyncMock(return_value=other)):
        result = await entry.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"device_ip": "192.168.1.77"}
        )
    assert result["errors"] == {"base": "wrong_machine"}


async def test_options_refuse_local_only_without_key(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, version=2, unique_id=DSN,
                            data={**V2_DATA, "lan_key": None, "refresh_token": "r"})  # fmt: skip
    entry.add_to_hass(hass)
    with patch("custom_components.cremalink_ha.async_setup_entry", return_value=True):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"connection": "lan", "lan_port": 10280}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"connection": "no_lan_key"}


async def test_options(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, version=2, unique_id=DSN, data=V2_DATA)
    entry.add_to_hass(hass)
    with patch("custom_components.cremalink_ha.async_setup_entry", return_value=True):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {"connection": "cloud", "lan_port": 10299, "advertise_ip": "192.168.1.10"},
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {
        "connection": "cloud", "lan_port": 10299, "advertise_ip": "192.168.1.10"
    }
