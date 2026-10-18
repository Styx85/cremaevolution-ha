"""Diagnostics: link health and machine state; secrets redacted."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import CremalinkConfigEntry
from .const import CONF_DEVICE_IP, CONF_EMAIL, CONF_LAN_KEY, CONF_PASSWORD, CONF_REFRESH_TOKEN

REDACT_KEYS = {CONF_EMAIL, CONF_PASSWORD, CONF_REFRESH_TOKEN, CONF_LAN_KEY, CONF_DEVICE_IP, "access_token"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: CremalinkConfigEntry
) -> dict[str, Any]:
    runtime = entry.runtime_data
    view = runtime.coordinator.data
    statistics = runtime.statistics_coordinator
    return {
        "entry_data": async_redact_data(dict(entry.data), REDACT_KEYS),
        "entry_options": async_redact_data(dict(entry.options), REDACT_KEYS),
        "profile": runtime.device.profile.family,
        "link": {"state": runtime.link.state, **asdict(runtime.link.stats)},
        "cloud_fallback_configured": runtime.cloud is not None,
        "cloud_fallbacks_used": runtime.device.cloud_fallbacks,
        "monitor": None
        if view is None
        else {
            **view.parsed,
            "status_name": view.status_name,
            "is_busy": view.is_busy,
            "received_at": view.received_at.isoformat(),
        },
        "statistics": None
        if statistics is None or not statistics.data
        else {
            "known": statistics.data.get("known"),
            "unknown_count": len(statistics.data.get("unknown", {})),
            "snapshot_fetched_at": statistics.data.get("snapshot_fetched_at"),
        },
    }
