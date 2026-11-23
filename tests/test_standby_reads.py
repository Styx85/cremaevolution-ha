"""Statistics and settings are not re-read in standby while nothing can change (#36)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from aioecam.exceptions import EcamError
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from custom_components.cremalink_ha.settings_coordinator import SettingsCoordinator
from custom_components.cremalink_ha.statistics_coordinator import StatisticsCoordinator

STANDBY, AWAKE = SimpleNamespace(status=0), SimpleNamespace(status=7)


def _device(state: Any) -> MagicMock:
    device = MagicMock()
    device.state = state
    device.is_busy = MagicMock(return_value=False)
    device.profile.settings = {"auto_off": None}
    device.read_setting = AsyncMock(return_value=3)
    device.read_statistics = AsyncMock(
        return_value=SimpleNamespace(known={"espresso": 1}, unknown={}, raw={})
    )
    return device


def _status(hass: HomeAssistant) -> DataUpdateCoordinator[None]:
    return DataUpdateCoordinator(
        hass, MagicMock(), config_entry=None, name="status", update_interval=None
    )


async def test_settings_skip_standby_until_the_machine_was_awake(hass: HomeAssistant) -> None:
    device = _device(STANDBY)
    settings = SettingsCoordinator(hass, device)
    status = _status(hass)
    settings.follow(status)

    await settings.async_refresh()
    await settings.async_refresh()
    assert device.read_setting.await_count == 1
    assert settings.standby_reads_skipped == 1

    device.state = AWAKE
    status.async_update_listeners()
    device.state = STANDBY
    status.async_update_listeners()
    await hass.async_block_till_done()
    await settings.async_refresh()  # the one read after waking up
    reads = device.read_setting.await_count
    await settings.async_refresh()
    assert device.read_setting.await_count == reads


async def test_settings_read_while_awake_or_unknown(hass: HomeAssistant) -> None:
    device = _device(None)
    settings = SettingsCoordinator(hass, device)
    await settings.async_refresh()
    await settings.async_refresh()
    device.state = AWAKE
    await settings.async_refresh()
    assert device.read_setting.await_count == 3


async def test_settings_failed_standby_read_is_retried(hass: HomeAssistant) -> None:
    device = _device(STANDBY)
    settings = SettingsCoordinator(hass, device)
    settings.async_set_updated_data({"auto_off": 3})
    device.read_setting.side_effect = [EcamError("busy"), 3]
    await settings.async_refresh()
    await settings.async_refresh()
    assert device.read_setting.await_count == 2


async def test_statistics_skip_standby_until_the_machine_was_awake(hass: HomeAssistant) -> None:
    device = _device(STANDBY)
    statistics = StatisticsCoordinator(hass, device)
    status = _status(hass)
    statistics.follow(status)

    await statistics.async_refresh()
    await statistics.async_refresh()
    assert device.read_statistics.await_count == 1
    assert statistics.standby_reads_skipped == 1

    device.state = AWAKE
    status.async_update_listeners()
    device.state = STANDBY
    await statistics.async_refresh()
    assert device.read_statistics.await_count == 2
    await statistics.async_refresh()
    assert device.read_statistics.await_count == 2


async def test_statistics_wake_during_a_read_keeps_the_next_read_due(
    hass: HomeAssistant,
) -> None:
    device = _device(STANDBY)
    statistics = StatisticsCoordinator(hass, device)
    status = _status(hass)
    statistics.follow(status)

    async def read(**_: Any) -> SimpleNamespace:
        device.state = AWAKE  # woken up at the panel mid-read
        status.async_update_listeners()
        device.state = STANDBY
        return SimpleNamespace(known={}, unknown={}, raw={})

    device.read_statistics.side_effect = read
    await statistics.async_refresh()
    device.read_statistics.side_effect = None
    await statistics.async_refresh()
    assert device.read_statistics.await_count == 2


async def test_manual_statistics_refresh_reads_in_standby(hass: HomeAssistant) -> None:
    device = _device(STANDBY)
    statistics = StatisticsCoordinator(hass, device)
    await statistics.async_refresh()
    await statistics.async_force_refresh()
    assert device.read_statistics.await_count == 2


async def test_failed_setting_write_reopens_standby_reads(hass: HomeAssistant) -> None:
    device = _device(STANDBY)
    settings = SettingsCoordinator(hass, device)
    await settings.async_refresh()
    device.write_setting = AsyncMock(side_effect=EcamError("no confirmation"))
    try:
        await settings.async_write("auto_off", 5)
    except EcamError:
        pass
    await settings.async_refresh()
    assert device.read_setting.await_count == 2


async def test_missing_cloud_service_properties_are_retried(hass: HomeAssistant) -> None:
    device = _device(STANDBY)
    cloud = MagicMock()
    cloud.read_property = AsyncMock(side_effect=EcamError("cloud busy"))
    statistics = StatisticsCoordinator(hass, device, cloud)
    await statistics.async_refresh()
    await statistics.async_refresh()
    assert device.read_statistics.await_count == 2
    cloud.read_property = AsyncMock(return_value=1)
    await statistics.async_refresh()
    await statistics.async_refresh()
    assert device.read_statistics.await_count == 3
    assert statistics.data["service_properties"]


async def test_failed_cloud_read_on_manual_refresh_reopens_standby_reads(
    hass: HomeAssistant,
) -> None:
    device = _device(STANDBY)
    cloud = MagicMock()
    cloud.read_property = AsyncMock(return_value=1)
    statistics = StatisticsCoordinator(hass, device, cloud)
    await statistics.async_refresh()
    cloud.read_property = AsyncMock(side_effect=EcamError("cloud busy"))
    await statistics.async_force_refresh()
    await statistics.async_refresh()
    assert device.read_statistics.await_count == 3
    assert statistics.data["service_properties"]  # last good values kept


async def test_settings_skip_rounds_while_the_machine_is_busy(hass: HomeAssistant) -> None:
    # Settings cannot change while rinsing or brewing, and the machine often
    # drops the request then: keep the last values, read again when idle.
    device = _device(AWAKE)
    settings = SettingsCoordinator(hass, device)
    await settings.async_refresh()
    assert device.read_setting.await_count == 1
    device.is_busy.return_value = True
    await settings.async_refresh()
    await settings.async_refresh()
    assert device.read_setting.await_count == 1
    assert settings.busy_reads_skipped == 2
    assert settings.data == {"auto_off": 3}
    device.is_busy.return_value = False
    await settings.async_refresh()
    assert device.read_setting.await_count == 2


async def test_first_settings_read_happens_even_when_busy(hass: HomeAssistant) -> None:
    device = _device(AWAKE)
    device.is_busy.return_value = True
    settings = SettingsCoordinator(hass, device)
    await settings.async_refresh()
    assert device.read_setting.await_count == 1
