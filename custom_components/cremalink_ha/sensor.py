"""Sensor platform for the Cremalink integration."""

from __future__ import annotations

import time

from homeassistant.components.sensor import (
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    EntityCategory,
    PERCENTAGE,
    UnitOfVolume,
)
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER


# Fast machine-monitor sensors.
SENSORS = [
    ("status_name", "Status", "mdi:coffee-maker", None),
    ("progress_percent", "Progress", "mdi:progress-clock", PERCENTAGE),
    ("accessory_name", "Accessory", "mdi:cup", None),
]


# Slow ECAM610 lifetime/statistics sensors.
#
# These values come from the live native A2 statistics table rather than
# the potentially stale Ayla d5xx/d7xx property cache.
STATISTICS_SENSORS = [
    # Overall totals
    (
        "total_beverages",
        "Total beverages",
        "mdi:counter",
        None,
    ),
    (
        "total_black_beverages",
        "Black beverages",
        "mdi:coffee",
        None,
    ),
    (
        "total_milk_beverages",
        "Milk beverages",
        "mdi:cup",
        None,
    ),
    (
        "total_other_beverages",
        "Other beverages",
        "mdi:counter",
        None,
    ),
    (
        "total_water_l",
        "Counted operating water",
        "mdi:water",
        UnitOfVolume.LITERS,
    ),
    (
        "water_since_filter_change_l",
        "Water since filter replacement",
        "mdi:water-check",
        UnitOfVolume.LITERS,
    ),

    # ECAM610 top-level internal categories
    (
        "total_milk_coffee_beverages",
        "Milk coffee category",
        "mdi:coffee",
        None,
    ),
    (
        "total_milk_only_beverages",
        "Milk only category",
        "mdi:cup",
        None,
    ),
    (
        "total_espressos",
        "Espressos total",
        "mdi:coffee",
        None,
    ),

    # Individual beverages
    (
        "espresso",
        "Espresso",
        "mdi:coffee",
        None,
    ),
    (
        "coffee",
        "Coffee",
        "mdi:coffee",
        None,
    ),
    (
        "long_coffee",
        "Long coffee",
        "mdi:coffee",
        None,
    ),
    (
        "doppio",
        "Doppio+",
        "mdi:coffee",
        None,
    ),
    (
        "americano",
        "Americano",
        "mdi:coffee",
        None,
    ),
    (
        "cappuccino",
        "Cappuccino",
        "mdi:coffee",
        None,
    ),
    (
        "latte_macchiato",
        "Latte Macchiato",
        "mdi:coffee",
        None,
    ),
    (
        "caffe_latte",
        "Caffè Latte",
        "mdi:coffee",
        None,
    ),
    (
        "flat_white",
        "Flat White",
        "mdi:coffee",
        None,
    ),
    (
        "espresso_macchiato",
        "Espresso Macchiato",
        "mdi:coffee",
        None,
    ),
    (
        "hot_milk",
        "Hot milk",
        "mdi:cup",
        None,
    ),
    (
        "cappuccino_doppio",
        "Cappuccino Doppio+",
        "mdi:coffee",
        None,
    ),
    (
        "cappuccino_mix",
        "Cappuccino Mix",
        "mdi:coffee",
        None,
    ),
    (
        "hot_water",
        "Hot water",
        "mdi:cup-water",
        None,
    ),
    (
        "tea",
        "Tea",
        "mdi:tea",
        None,
    ),
    (
        "coffee_pot",
        "Coffee pot",
        "mdi:coffee-maker",
        None,
    ),

    (
        "espresso_soul",
        "Espresso SOUL",
        "mdi:coffee",
        None,
    ),
    (
        "over_ice",
        "Over Ice",
        "mdi:cup",
        None,
    ),
    (
        "custom_milk_coffee_beverages",
        "Custom milk-coffee beverages",
        "mdi:coffee",
        None,
    ),

    # Maintenance
    (
        "descale_load_raw",
        "Descale load (raw)",
        "mdi:shimmer",
        None,
    ),
    (
        "descale_count",
        "Descales",
        "mdi:shimmer",
        None,
    ),
    (
        "filter_replacements",
        "Filter replacements",
        "mdi:water-check",
        None,
    ),
    (
        "grounds_container_clean_count",
        "Grounds container cleanings",
        "mdi:delete-empty",
        None,
    ),
]


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up Cremalink sensors."""

    data = entry.runtime_data

    coordinator = data.coordinator
    statistics_coordinator = data.statistics_coordinator

    entities = []

    for key, name, icon, unit in SENSORS:
        entities.append(
            CremalinkSensor(
                coordinator,
                entry,
                key,
                name,
                icon,
                unit,
            )
        )

    # Temporary fork-only monitor debugging aid.
    entities.append(
        CremalinkMonitorDiagnosticsSensor(
            coordinator,
            entry,
        )
    )

    if statistics_coordinator is not None:
        for key, name, icon, unit in STATISTICS_SENSORS:
            entities.append(
                CremalinkStatisticsSensor(
                    statistics_coordinator,
                    entry,
                    key,
                    name,
                    icon,
                    unit,
                )
            )

        # Keep the complete reverse-engineering data accessible without
        # cluttering normal installations or recording large attributes
        # by default.
        entities.append(
            CremalinkStatisticsDiagnosticsSensor(
                statistics_coordinator,
                entry,
            )
        )

    async_add_entities(entities)


class CremalinkSensor(CoordinatorEntity, SensorEntity):
    """Representation of a normal Cremalink monitor sensor."""

    def __init__(
        self,
        coordinator,
        entry,
        key,
        name,
        icon,
        unit,
    ):
        """Initialize the sensor."""

        super().__init__(coordinator)

        self._key = key
        self._attr_has_entity_name = True
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_icon = icon
        self._attr_native_unit_of_measurement = unit

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer=MANUFACTURER,
        )

    @property
    def available(self):
        """Return whether the sensor is available."""

        if not self.coordinator.data:
            return False

        return super().available

    @property
    def native_value(self):
        """Return the current monitor value."""

        return getattr(
            self.coordinator.data,
            self._key,
            None,
        )


class CremalinkMonitorDiagnosticsSensor(
    CoordinatorEntity,
    SensorEntity,
):
    """Expose the exact monitor snapshot currently used by Home Assistant."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_icon = "mdi:bug-check"

    def __init__(self, coordinator, entry):
        """Initialize the temporary monitor diagnostics sensor."""
        super().__init__(coordinator)

        self._attr_name = f"{entry.title} Monitor diagnostics"
        self._attr_unique_id = (
            f"{entry.entry_id}_monitor_diagnostics"
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer=MANUFACTURER,
        )

    @property
    def available(self):
        """Return whether at least one monitor snapshot exists."""
        return self.coordinator.data is not None

    @property
    def native_value(self):
        """Use receive time as state so stale frames are obvious."""
        data = self.coordinator.data
        received_at = getattr(data, "received_at", None)

        if received_at is None:
            return None

        return received_at.isoformat()

    @property
    def extra_state_attributes(self):
        """Expose raw, parsed and derived values from one snapshot."""
        data = self.coordinator.data

        if data is None:
            return {}

        snapshot = getattr(data, "snapshot", None)
        received_at = getattr(data, "received_at", None)

        frame_age_seconds = None
        if received_at is not None:
            try:
                frame_age_seconds = round(
                    max(0.0, time.time() - received_at.timestamp()),
                    3,
                )
            except Exception:
                frame_age_seconds = None

        interval = getattr(
            self.coordinator,
            "update_interval",
            None,
        )
        interval_seconds = None
        if interval is not None:
            try:
                interval_seconds = interval.total_seconds()
            except Exception:
                interval_seconds = None

        available_fields = list(
            getattr(data, "available_fields", []) or []
        )
        derived = {}
        for field in available_fields:
            try:
                derived[field] = getattr(data, field)
            except Exception as err:
                derived[field] = f"<error: {err}>"

        parsed = dict(getattr(data, "parsed", {}) or {})

        return {
            "received_at": (
                received_at.isoformat()
                if received_at is not None
                else None
            ),
            "frame_age_seconds": frame_age_seconds,
            "source": getattr(snapshot, "source", None),
            "device_id": getattr(snapshot, "device_id", None),
            "coordinator_last_update_success": getattr(
                self.coordinator,
                "last_update_success",
                None,
            ),
            "coordinator_update_interval_seconds": interval_seconds,
            "monitor_consecutive_failures": getattr(
                self.coordinator,
                "monitor_consecutive_failures",
                None,
            ),
            "raw_b64": getattr(data, "raw_b64", None),
            "raw_hex": (
                getattr(data, "raw", b"").hex()
                if getattr(data, "raw", None)
                else ""
            ),
            "warnings": list(
                getattr(snapshot, "warnings", []) or []
            ),
            "errors": list(
                getattr(snapshot, "errors", []) or []
            ),
            "parsed": parsed,
            "status": parsed.get("status"),
            "status_name": getattr(data, "status_name", None),
            "action": parsed.get("action"),
            "action_name": getattr(data, "action_name", None),
            "progress": parsed.get("progress"),
            "accessory": parsed.get("accessory"),
            "accessory_name": getattr(
                data,
                "accessory_name",
                None,
            ),
            "alarms": parsed.get("alarms"),
            "switches": parsed.get("switches"),
            "derived": derived,
            "is_busy": derived.get("is_busy"),
            "is_idle": derived.get("is_idle"),
            "is_watertank_empty": derived.get(
                "is_watertank_empty"
            ),
            "is_watertank_open": derived.get(
                "is_watertank_open"
            ),
            "is_waste_container_full": derived.get(
                "is_waste_container_full"
            ),
            "is_waste_container_missing": derived.get(
                "is_waste_container_missing"
            ),
            "is_milk_fridge_warning": derived.get(
                "is_milk_fridge_warning"
            ),
        }


class CremalinkStatisticsSensor(
    CoordinatorEntity,
    SensorEntity,
):
    """Representation of a live ECAM610 A2 statistic."""

    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(
        self,
        coordinator,
        entry,
        key,
        name,
        icon,
        unit,
    ):
        """Initialize an ECAM610 statistics sensor."""

        super().__init__(coordinator)

        self._key = key
        self._attr_has_entity_name = True
        self._attr_translation_key = key
        self._attr_unique_id = (
            f"{entry.entry_id}_statistics_{key}"
        )
        self._attr_icon = icon
        self._attr_native_unit_of_measurement = unit

        if key == "descale_load_raw":
            # Weighted maintenance/load accumulator with an intentional
            # reset after successful descaling. Its unit and weighting
            # formula are not established, so do not expose it as a
            # Home Assistant long-term increasing total.
            self._attr_state_class = None

        if key == "total_water_l":
            self._attr_suggested_display_precision = 1
        elif key == "water_since_filter_change_l":
            self._attr_suggested_display_precision = 2

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer=MANUFACTURER,
        )

    @property
    def available(self):
        """Return whether statistics are currently available."""

        data = self.coordinator.data

        if not isinstance(data, dict):
            return False

        known = data.get("known")

        if not isinstance(known, dict):
            return False

        # Lifetime/statistics values are slow-changing.  A transient
        # coordinator failure must not discard the last successfully
        # received value or mark the entity unavailable.
        return self._key in known

    @property
    def native_value(self):
        """Return the semantic A2 statistic."""

        data = self.coordinator.data or {}
        known = data.get("known", {})

        return known.get(self._key)


class CremalinkStatisticsDiagnosticsSensor(
    CoordinatorEntity,
    SensorEntity,
):
    """Diagnostic access to complete raw/unknown A2 statistics."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    # Avoid recording the complete raw statistics dictionary unless the
    # user explicitly enables this diagnostic entity.
    _attr_entity_registry_enabled_default = False

    _attr_icon = "mdi:code-braces"

    def __init__(
        self,
        coordinator,
        entry,
    ):
        """Initialize the raw statistics diagnostic entity."""

        super().__init__(coordinator)

        self._attr_has_entity_name = True
        self._attr_translation_key = "a2_statistics_diagnostics"
        self._attr_unique_id = (
            f"{entry.entry_id}_statistics_diagnostics"
        )

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer=MANUFACTURER,
        )

    @property
    def available(self):
        """Return whether a statistics snapshot exists."""

        return (
            isinstance(self.coordinator.data, dict)
            and super().available
        )

    @property
    def native_value(self):
        """Return the number of currently unknown A2 IDs."""

        data = self.coordinator.data or {}
        unknown = data.get("unknown", {})

        return len(unknown)

    @property
    def extra_state_attributes(self):
        """Expose unknown and complete raw A2 tables."""

        data = self.coordinator.data or {}

        unknown = data.get("unknown", {})
        raw = data.get("raw", {})

        progress = getattr(
            self.coordinator,
            "a2_progress",
            None,
        ) or {}

        # String keys are friendlier for HA's JSON/state machinery.
        return {
            "unknown_statistics": {
                str(key): value
                for key, value in unknown.items()
            },
            "raw_statistics": {
                str(key): value
                for key, value in raw.items()
            },
            "service_properties": data.get(
                "service_properties",
                {},
            ),
            "raw_count": len(raw),
            "snapshot_fetched_at": data.get("snapshot_fetched_at"),
            "refresh_in_progress": getattr(
                self.coordinator,
                "refresh_in_progress",
                False,
            ),
            "refresh_started_at": getattr(
                self.coordinator,
                "refresh_started_at",
                None,
            ),
            "refresh_running_for_seconds": getattr(
                self.coordinator,
                "refresh_running_for_seconds",
                None,
            ),
            "last_refresh_duration_seconds": getattr(
                self.coordinator,
                "last_refresh_duration_seconds",
                None,
            ),
            "a2_phase": progress.get("phase"),
            "a2_page": progress.get("page"),
            "a2_start_id": progress.get("start_id"),
            "a2_request_count": progress.get("request_count"),
            "a2_returned_count": progress.get("returned_count"),
            "a2_last_id": progress.get("last_id"),
            "a2_collected_count": progress.get("collected_count"),
        }
