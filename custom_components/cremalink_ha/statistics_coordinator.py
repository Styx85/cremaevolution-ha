"""Lifetime statistics (A2) coordinator.

The A2 table is read locally over the LAN session (a full read took about 7 s
on an ECAM610.75, also in standby). If a cloud token is configured, the cloud
service properties are added for comparison.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from aioecam.cloud.api import AylaCloud
from aioecam.device import EcamDevice
from aioecam.exceptions import CommandTimeout, EcamError
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STATISTICS_UPDATE_INTERVAL = timedelta(minutes=10)
SERVICE_PROPERTIES = (
    "d550_water_calc_qty",
    "d555_water_filter_qty",
    "d556_water_hardness",
    "d512_percentage_to_deca",
    "d513_percentage_usage_fltr",
)


def statistics_supported(device: EcamDevice) -> bool:
    """A2 semantics are hardware-verified for the PrimaDonna Soul family only."""
    return device.profile.family == "ecam61x"


class StatisticsCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(
        self, hass: HomeAssistant, device: EcamDevice, cloud: AylaCloud | None = None
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_statistics",
            update_interval=STATISTICS_UPDATE_INTERVAL,
        )
        self.device = device
        self.cloud = cloud
        self.refresh_in_progress = False
        self.refresh_started_at: str | None = None
        self.last_refresh_duration_seconds: float | None = None
        self.a2_progress: dict[str, Any] | None = None
        self._started: float | None = None

    @property
    def refresh_running_for_seconds(self) -> float | None:
        if not self.refresh_in_progress or self._started is None:
            return None
        return round(time.monotonic() - self._started, 1)

    def _on_progress(self, progress: dict[str, int]) -> None:
        self.a2_progress = dict(progress)
        self.async_update_listeners()

    async def async_force_refresh(self) -> None:
        """Manual refresh from the diagnostics button."""
        if self.refresh_in_progress:
            return
        self.refresh_in_progress = True
        self.refresh_started_at = datetime.now(UTC).isoformat()
        self._started = time.monotonic()
        self.last_refresh_duration_seconds = None
        self.a2_progress = None
        self.async_update_listeners()
        try:
            await self.async_refresh()
        finally:
            self.last_refresh_duration_seconds = round(time.monotonic() - self._started, 1)
            self.refresh_in_progress = False
            self._started = None
            self.async_update_listeners()

    async def _service_properties(self) -> dict[str, Any]:
        if self.cloud is None:
            return {}
        result: dict[str, Any] = {}
        for name in SERVICE_PROPERTIES:
            try:
                result[name] = await self.cloud.read_property(self.device.link.dsn, name)
            except EcamError as err:
                _LOGGER.debug("could not read cloud service property %s: %s", name, err)
                return {}
        return result

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            snapshot = await self.device.read_statistics(progress=self._on_progress)
        except CommandTimeout as err:
            # Lifetime counters change slowly: keep the last complete table.
            if self.data:
                _LOGGER.debug("A2 read timed out, keeping previous snapshot: %s", err)
                return self.data
            raise UpdateFailed(f"error reading statistics: {err}") from err
        except EcamError as err:
            raise UpdateFailed(f"error reading statistics: {err}") from err
        return {
            "known": snapshot.known,
            "unknown": snapshot.unknown,
            "raw": snapshot.raw,
            "service_properties": await self._service_properties(),
            # Stamped only on complete reads; a retained snapshot keeps its time.
            "snapshot_fetched_at": datetime.now(UTC).isoformat(),
        }
