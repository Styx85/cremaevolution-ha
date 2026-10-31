"""Cremalink for Home Assistant: local-first control of De'Longhi Coffee Link machines.

Talks to the machine directly over Ayla LAN mode through aioecam; the
Cremalink Server add-on is no longer needed. Machines without LAN mode run
through the De'Longhi cloud instead (slower, fewer features).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from aioecam.cloud.api import AylaCloud
from aioecam.cloud.link import CloudLink
from aioecam.cloud.region import load_region
from aioecam.device import EcamDevice
from aioecam.ecam.profile import load_profile
from aioecam.exceptions import EcamError, NotConnected
from aioecam.lan.link import LanLink
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryError,
    ConfigEntryNotReady,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.typing import ConfigType

from .const import (
    DOMAIN,
    CLOUD_FAST_INTERVAL,
    CLOUD_KEEPALIVE_INTERVAL,
    CLOUD_POLL_INTERVAL,
    CONF_ADVERTISE_IP,
    CONF_CONNECTION,
    CONF_DEVICE_IP,
    CONF_DSN,
    CONF_KEY_ID,
    CONF_LAN_KEY,
    CONF_LAN_PORT,
    CONF_PROFILE,
    CONF_REFRESH_TOKEN,
    CONNECTION_AUTO,
    CONNECTION_CLOUD,
    DEFAULT_LAN_PORT,
    DEFAULT_PROFILE,
    LEGACY_CONF_ADDON_URL,
    LEGACY_CONF_CONNECTION_TYPE,
    LEGACY_CONF_DEVICE_MAP,
    LEGACY_CONF_TOKEN_FILE,
    LEGACY_DEVICE_MAP_TO_PROFILE,
)
from .coordinator import EcamCoordinator
from .firmware import async_update_firmware
from .recipe_reader import RecipeReader, supported as recipe_reads_supported
from .runtime import RuntimeData, async_acquire_server, async_release_server
from .services import async_setup_services
from .settings_coordinator import SettingsCoordinator, settings_supported
from .statistics_coordinator import StatisticsCoordinator, statistics_supported

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.SWITCH,
    Platform.BUTTON,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SELECT,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type CremalinkConfigEntry = ConfigEntry[RuntimeData]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration's services once, independent of entries."""
    async_setup_services(hass)
    return True


def _use_cloud(entry: CremalinkConfigEntry) -> bool:
    """LAN first: the cloud only when forced or when there is no LAN key."""
    mode = entry.options.get(CONF_CONNECTION, CONNECTION_AUTO)
    has_lan = bool(entry.data.get(CONF_LAN_KEY) and entry.data.get(CONF_DEVICE_IP))
    return mode == CONNECTION_CLOUD or (mode == CONNECTION_AUTO and not has_lan)


async def async_setup_entry(hass: HomeAssistant, entry: CremalinkConfigEntry) -> bool:
    data = entry.data
    use_cloud = _use_cloud(entry)
    if use_cloud and not data.get(CONF_REFRESH_TOKEN):
        # Cloud mode, or a migrated cloud-only entry: needs a login once.
        raise ConfigEntryAuthFailed("no LAN key and no cloud sign-in stored; sign in again")
    if not use_cloud and not (data.get(CONF_LAN_KEY) and data.get(CONF_DEVICE_IP)):
        # Signing in again would not help: the cloud has no LAN key for it.
        raise ConfigEntryError(
            "local connection chosen, but this machine has no local key; "
            "choose Automatic or De'Longhi cloud in the options"
        )

    cloud = None
    if data.get(CONF_REFRESH_TOKEN):

        def _store_token(token: str) -> None:
            # Persist the rotating token. This must not reload the entry: only
            # option changes do (OptionsFlowWithReload), otherwise every token
            # refresh would restart the integration in a loop.
            hass.config_entries.async_update_entry(
                entry, data={**entry.data, CONF_REFRESH_TOKEN: token}
            )

        cloud = AylaCloud(
            async_get_clientsession(hass),
            load_region("eu"),
            data[CONF_REFRESH_TOKEN],
            on_refresh_token=_store_token,
        )
    profile = load_profile(data.get(CONF_PROFILE, DEFAULT_PROFILE))

    link: LanLink | CloudLink
    if use_cloud:
        assert cloud is not None
        link = CloudLink(
            cloud,
            dsn=data[CONF_DSN],
            device_ip=data.get(CONF_DEVICE_IP),
            watch=[profile.monitor_property, profile.properties["data_response"]],
            interval=CLOUD_POLL_INTERVAL,
            fast_interval=CLOUD_FAST_INTERVAL,
        )
        link.start()
    else:
        server = await async_acquire_server(
            hass,
            entry.entry_id,
            entry.options.get(CONF_LAN_PORT, DEFAULT_LAN_PORT),
            entry.options.get(CONF_ADVERTISE_IP) or None,
        )
        link = server.add_link(
            device_ip=data[CONF_DEVICE_IP],
            lan_key=data[CONF_LAN_KEY],
            dsn=data[CONF_DSN],
            key_id=data.get(CONF_KEY_ID),
        )

    try:
        # In cloud mode the link is the cloud: no separate wake fallback.
        device = EcamDevice(link, profile, cloud=None if use_cloud else cloud)
        try:
            await link.wait_active(20 if use_cloud else 15)
        except NotConnected as err:
            if isinstance(link, CloudLink):
                if link.stats.auth_failed:
                    raise ConfigEntryAuthFailed("the cloud rejected the sign-in") from err
                raise ConfigEntryNotReady(f"no connection through the cloud: {err}") from err
            hint = ""
            if link.stats.last_registration_status == 404:
                hint = " (the Wi-Fi module refuses LAN mode until it has reached the cloud)"
            raise ConfigEntryNotReady(f"no LAN session with the machine{hint}") from err

        if use_cloud:
            await _async_start_cloud_keepalive(hass, entry, device)

        coordinator = EcamCoordinator(hass, device, cloud=use_cloud)
        await coordinator.async_config_entry_first_refresh()

        # Cloud mode skips the features that cost many cloud requests.
        statistics = None
        if not use_cloud and statistics_supported(device):
            statistics = StatisticsCoordinator(hass, device, cloud)
            # Optional and slower than the status; do not hold up setup.
            entry.async_create_background_task(
                hass, statistics.async_refresh(), "cremalink initial statistics"
            )

        settings = None
        if not use_cloud and settings_supported(device):
            settings = SettingsCoordinator(hass, device)
            entry.async_on_unload(settings.follow(coordinator))
            # Readable in standby; do not hold up setup.
            entry.async_create_background_task(
                hass, settings.async_refresh(), "cremalink initial settings"
            )

        entry.runtime_data = RuntimeData(
            device=device,
            link=link,
            coordinator=coordinator,
            statistics_coordinator=statistics,
            settings_coordinator=settings,
            cloud=cloud,
            recipe_reader=RecipeReader(device, coordinator.async_update_listeners)
            if not use_cloud and recipe_reads_supported(device) else None,
        )
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        # Diagnostic only; do not hold up setup.
        entry.async_create_background_task(
            hass, async_update_firmware(hass, entry, device), "cremalink firmware version"
        )
        return True
    except BaseException:
        # Any failed setup, including a timed-out first refresh or cancellation,
        # must release the link and our share of the LAN server; otherwise the
        # retry registers on top of a leftover link (Codex review A3).
        await _async_release_link(hass, entry, link)
        raise


async def _async_start_cloud_keepalive(
    hass: HomeAssistant, entry: CremalinkConfigEntry, device: EcamDevice
) -> None:
    """Without it the cloud stops relaying commands after about 5 minutes."""

    async def _keep(_now: Any = None) -> None:
        try:
            await device.keep_cloud_session()
        except EcamError as err:
            _LOGGER.debug("cloud keep-alive failed: %s", err)

    await _keep()
    entry.async_on_unload(async_track_time_interval(hass, _keep, CLOUD_KEEPALIVE_INTERVAL))


async def _async_release_link(
    hass: HomeAssistant, entry: CremalinkConfigEntry, link: LanLink | CloudLink
) -> None:
    if isinstance(link, CloudLink):
        await link.close()
        return
    hass_data = hass.data.get(DOMAIN, {})
    if "lan" in hass_data:
        await hass_data["lan"].server.remove_link(link.device_ip)
    await async_release_server(hass, entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: CremalinkConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        # Home Assistant shuts the coordinators down itself.
        await _async_release_link(hass, entry, entry.runtime_data.link)
    return unloaded


def _read_legacy_refresh_token(path: str) -> str | None:
    try:
        token = json.loads(Path(path).read_text()).get("refresh_token")
    except (OSError, ValueError):
        return None
    return token if isinstance(token, str) and token else None


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """v1 (cremalink + add-on) -> v2 (aioecam, no add-on).

    The entry keeps its id, so every entity keeps its unique id and history.
    """
    if entry.version > 2:
        return False  # downgrade from a newer version
    if entry.version == 2:
        return True

    old = dict(entry.data)
    device_map = str(old.get(LEGACY_CONF_DEVICE_MAP, "")).upper().removesuffix(".JSON")
    profile = LEGACY_DEVICE_MAP_TO_PROFILE.get(device_map)
    if profile is None:
        _LOGGER.warning(
            "unknown device map %r, using the %s profile; reconfigure if this is wrong",
            old.get(LEGACY_CONF_DEVICE_MAP),
            DEFAULT_PROFILE,
        )
        profile = DEFAULT_PROFILE

    new: dict[str, Any] = {
        CONF_DSN: old[CONF_DSN],
        CONF_DEVICE_IP: old.get(CONF_DEVICE_IP),
        CONF_LAN_KEY: old.get(CONF_LAN_KEY),
        CONF_PROFILE: profile,
    }
    if token_file := old.get(LEGACY_CONF_TOKEN_FILE):
        token = await hass.async_add_executor_job(_read_legacy_refresh_token, token_file)
        if token:
            new[CONF_REFRESH_TOKEN] = token
    dropped = [k for k in (LEGACY_CONF_ADDON_URL, LEGACY_CONF_CONNECTION_TYPE) if k in old]
    hass.config_entries.async_update_entry(entry, data=new, version=2)
    _LOGGER.info(
        "migrated %s to the add-on-free version (profile %s, dropped %s)",
        entry.title,
        profile,
        ", ".join(dropped) or "nothing",
    )
    return True
