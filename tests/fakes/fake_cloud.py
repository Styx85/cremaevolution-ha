"""An in-memory stand-in for AylaCloud with a simulated machine behind it.

FakeMachine sees the surface of a FakeDevice (``properties``, ``on_write``,
``push``); Home Assistant sees the AylaCloud methods the cloud link uses.
Every value records when it changed, like the cloud's ``data_updated_at``.
"""

from __future__ import annotations

import asyncio
import itertools
from collections.abc import Awaitable, Callable
from typing import Any


class _Properties(dict[str, str]):
    def __init__(self) -> None:
        super().__init__()
        self.updated_at: dict[str, str] = {}
        self._clock = itertools.count(1)

    def __setitem__(self, name: str, value: str) -> None:
        super().__setitem__(name, value)
        self.updated_at[name] = f"2026-10-31T08:{next(self._clock):05d}Z"


class MemoryCloud:
    def __init__(self) -> None:
        self.properties = _Properties()
        self.on_write: Callable[[str, str], Awaitable[None]] | None = None
        self.writes: list[tuple[str, str]] = []
        self.reads = 0
        self.refresh_token = "refresh-1"
        self._tasks: set[asyncio.Task[None]] = set()

    # --- what FakeMachine uses -------------------------------------------------

    async def push(self, name: str, value: str) -> int:
        self.properties[name] = value
        return 200

    # --- what the integration uses (AylaCloud) --------------------------------

    async def read_properties(self, dsn: str, names: list[str]) -> dict[str, dict[str, Any]]:
        self.reads += 1
        return {
            n: {"name": n, "value": self.properties[n], "base_type": "string",
                "data_updated_at": self.properties.updated_at[n]}  # fmt: skip
            for n in names
            if n in self.properties
        }

    async def read_property(self, dsn: str, name: str) -> Any:
        self.reads += 1
        return self.properties.get(name)

    async def write_property(self, dsn: str, name: str, value: str) -> None:
        self.writes.append((name, value))
        if self.on_write:
            # The cloud relays the write; the machine handles it on its own time.
            task = asyncio.get_running_loop().create_task(self.on_write(name, value))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
