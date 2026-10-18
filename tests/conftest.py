"""Test fixtures: a real Home Assistant core and a simulated machine on localhost."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from aioecam.lan.server import LanServer

from custom_components.cremalink_ha import runtime
from tests.fakes.fake_device import FakeDevice
from tests.fakes.fake_machine import FakeMachine

pytest_plugins = "pytest_homeassistant_custom_component"

LAN_KEY = "dGVzdGtleXRlc3RrZXl0ZXN0a2V5dGVzdA=="
DSN = "AC000W000000001"
IP = "127.0.0.1"  # the HA test harness only allows localhost sockets
TABLE = {100: 981155, 101: 410, 105: 30, 106: 2736033, 108: 22, 109: 54380, 111: 18,
         115: 4325, 116: 1632, 3000: 396, 3001: 5231, 3002: 2, 3003: 180}  # fmt: skip

V2_DATA = {"dsn": DSN, "device_ip": IP, "lan_key": LAN_KEY, "profile": "ecam61x"}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    enable_custom_integrations: Any, socket_enabled: Any
) -> Iterator[None]:
    # The simulated machine is a real network peer on localhost.
    yield


@pytest.fixture
async def machine() -> AsyncIterator[FakeMachine]:
    device = FakeDevice(ip=IP, lan_key=LAN_KEY)
    device.dsn = DSN
    await device.start()
    sim = FakeMachine(device, statistics=TABLE)
    sim.step = 0.3
    yield sim
    await device.stop()


@pytest.fixture(autouse=True)
def local_network(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    """Run the integration's LAN server on a free localhost port with fast timings."""

    class TestServer(LanServer):
        def __init__(self, http: Any, *, port: int, advertise_ip: str | None) -> None:
            super().__init__(http, host=IP, port=0, advertise_ip=IP)

        def add_link(self, **kwargs: Any) -> Any:
            sim: FakeMachine | None = (
                request.getfixturevalue("machine") if "machine" in request.fixturenames else None
            )
            kwargs.setdefault("device_port", sim.device.port if sim else 9)
            kwargs.update(keepalive=1.0, probe_timeout=1.0, register_timeout=1.0, max_backoff=0.5)
            return super().add_link(**kwargs)

    monkeypatch.setattr(runtime, "LanServer", TestServer)
