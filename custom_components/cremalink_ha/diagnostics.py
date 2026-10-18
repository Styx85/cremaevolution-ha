"""Diagnostics: link health and machine state; secrets redacted."""

from __future__ import annotations

import time
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import CremalinkConfigEntry
from .const import (
    CONF_DEVICE_IP,
    CONF_EMAIL,
    CONF_LAN_KEY,
    CONF_PASSWORD,
    CONF_REFRESH_TOKEN,
)

REDACT_KEYS = {CONF_EMAIL, CONF_PASSWORD, CONF_REFRESH_TOKEN, CONF_LAN_KEY, CONF_DEVICE_IP, "access_token", "dsn", "advertise_ip"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: CremalinkConfigEntry
) -> dict[str, Any]:
    runtime = entry.runtime_data
    view = runtime.coordinator.data
    statistics = runtime.statistics_coordinator
    device = runtime.device

    def stamp(value: float | None) -> str | None:
        return None if value is None else datetime.fromtimestamp(value, UTC).isoformat()

    now = time.time()
    return {
        "identity": {
            "transport": "ayla_lan",
            "model": device.machine_info.get("d561_machine_model"),
            "firmware": device.machine_info.get("software_version")
            or device.machine_info.get("d560_versione_sw"),
            "configured_protocol_family": device.profile.family,
            "oem_model": entry.data.get("oem_model"),
            "oem_model_source": "onboarding" if entry.data.get("oem_model") else None,
            "observed_machine_properties": dict(device.machine_info),
            "scope": "available_metadata_only; protocol family is not an exact model",
        },
        "freshness": {
            "last_datapoint_received_at": stamp(device.last_datapoint_at),
            "last_valid_monitor_received_at": stamp(device.last_monitor_at),
            "monitor_receive_age_seconds": None if device.last_monitor_at is None
            else max(0, now - device.last_monitor_at),
            "monitor_payload_timestamp": None if device.state is None else device.state.timestamp,
            "monitor_query_started_at": stamp(device.monitor_query_started_at),
            "monitor_query_finished_at": stamp(device.monitor_query_finished_at),
            "monitor_query_result": device.monitor_query_result,
            "monitor_query_error": device.monitor_query_error,
            "last_monitor_error_at": stamp(device.last_monitor_error_at),
            "last_monitor_error": device.last_monitor_error,
            "consecutive_query_failures": runtime.coordinator.monitor_consecutive_failures,
            "semantics": "receipt/query success does not prove new machine state; standby may cache",
        },
        "recipe_catalog": device.catalog.snapshot(),
        "recipe_read": None if runtime.recipe_reader is None else runtime.recipe_reader.snapshot(),
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
            "received_at": stamp(device.last_monitor_at),
            "view_created_at": view.received_at.isoformat(),
        },
        "statistics": None
        if statistics is None or not statistics.data
        else {
            "known": statistics.data.get("known"),
            "unknown_count": len(statistics.data.get("unknown", {})),
            "snapshot_fetched_at": statistics.data.get("snapshot_fetched_at"),
        },
    }
