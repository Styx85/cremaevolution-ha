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
from aioecam.lan.link import LinkState
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .monitor_view import MonitorView

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL_AWAKE = timedelta(seconds=2)
SCAN_INTERVAL_STANDBY = timedelta(seconds=30)
# Cloud mode: the cloud link polls by itself and pushes changes; this is only
# a slow safety read.
SCAN_INTERVAL_CLOUD = timedelta(minutes=5)

# Keep the last good state across a short run of failed reads so entities do
# not flap to unavailable; a sustained outage still surfaces.
TRANSIENT_FAILURE_LIMIT = 2


class EcamCoordinator(DataUpdateCoordinator[MonitorView]):
    def __init__(self, hass: HomeAssistant, device: EcamDevice, *, cloud: bool = False) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL_CLOUD if cloud else SCAN_INTERVAL_AWAKE,
        )
        self.device = device
        self.cloud = cloud
        self.monitor_consecutive_failures = 0
        self._unsubscribe = device.subscribe(self._on_push)
        self._unsubscribe_link = lambda: None
        on_state = getattr(device.link, "on_state", None)
        if cloud and on_state is not None:
            # The cloud link knows within one poll when the machine went
            # offline; do not show its last values for another 15 minutes.
            self._unsubscribe_link = on_state(self._on_link_state)

    def _view(self, state: MachineState) -> MonitorView:
        view = MonitorView(state=state, profile=self.device.profile, received_at=dt_util.utcnow())
        if self.cloud:
            self.update_interval = SCAN_INTERVAL_CLOUD
        else:
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

    @callback
    def _on_link_state(self, state: LinkState) -> None:
        if state is LinkState.ACTIVE:
            self.hass.async_create_task(self.async_request_refresh())
        elif state is not LinkState.CLOSED and self.last_update_success:
            self.async_set_update_error(UpdateFailed(f"cloud link {state}"))

    async def _async_update_data(self) -> MonitorView:
        if getattr(getattr(self.device.link, "stats", None), "auth_failed", False):
            # Cloud mode: a revoked sign-in only comes back with a new login.
            raise ConfigEntryAuthFailed("the cloud rejected the sign-in")
        try:
            # The timeout covers the whole request including the module fetching
            # it (aioecam >= 0.1.2b1), so leave room for a slow Wi-Fi round trip.
            state = await self.device.request_monitor(timeout=5)
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
        self._unsubscribe_link()
        await super().async_shutdown()
