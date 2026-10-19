"""Machine state coordinator.

The machine does not reliably push changes, so the coordinator polls: fast
while the machine is awake, slow in standby. Each poll is an explicit monitor
request (0x75) that waits for a fresh frame: with its internet access blocked
the Wi-Fi module published no state changes at all, and a plain read kept
returning the frame from before a wake-up (measured 2026-10-19). Pushes that
do arrive are applied immediately.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from aioecam.device import EcamDevice
from aioecam.ecam.monitor import MachineState
from aioecam.exceptions import EcamError
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .monitor_view import MonitorView

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL_AWAKE = timedelta(seconds=2)
SCAN_INTERVAL_STANDBY = timedelta(seconds=30)

# Keep the last good state across a short run of failed reads so entities do
# not flap to unavailable; a sustained outage still surfaces.
TRANSIENT_FAILURE_LIMIT = 2


class EcamCoordinator(DataUpdateCoordinator[MonitorView]):
    def __init__(self, hass: HomeAssistant, device: EcamDevice) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=SCAN_INTERVAL_AWAKE)
        self.device = device
        self.monitor_consecutive_failures = 0
        self._unsubscribe = device.subscribe(self._on_push)

    def _view(self, state: MachineState) -> MonitorView:
        view = MonitorView(state=state, profile=self.device.profile, received_at=dt_util.utcnow())
        self.update_interval = (
            SCAN_INTERVAL_STANDBY if state.status == 0 else SCAN_INTERVAL_AWAKE
        )
        return view

    @callback
    def _on_push(self, state: MachineState) -> None:
        # Called for reads we triggered as well; only publish real changes.
        if self.data is None or self.data.state != state:
            self.monitor_consecutive_failures = 0
            self.async_set_updated_data(self._view(state))

    async def _async_update_data(self) -> MonitorView:
        try:
            state = await self.device.request_monitor(timeout=3)
        except EcamError as err:
            self.monitor_consecutive_failures += 1
            if self.data is not None and self.monitor_consecutive_failures <= TRANSIENT_FAILURE_LIMIT:
                _LOGGER.debug(
                    "transient monitor failure %s/%s, keeping last state: %s",
                    self.monitor_consecutive_failures,
                    TRANSIENT_FAILURE_LIMIT,
                    err,
                )
                return self.data
            raise UpdateFailed(f"no state from the machine: {err}") from err
        self.monitor_consecutive_failures = 0
        if self.data is not None and self.data.state == state:
            return self.data
        return self._view(state)

    async def async_shutdown(self) -> None:
        self._unsubscribe()
        await super().async_shutdown()
