"""Opt-in, bounded reads of Soul catalogue properties over the existing link."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aioecam.device import EcamDevice
from aioecam.exceptions import EcamError

# Property names observed in our ECAM610.75 cloud snapshot (2026-10-18).
# Load outside the event loop; no cloud catalogue discovery or recipe writes.
SOUL_PROPERTIES = tuple(
    json.loads(Path(__file__).with_name("recipe_properties_ecam61x.json").read_text())
) + (
    "d560_versione_sw",
    "d561_machine_model",
    "d562_versione_sw_ui",
    "software_version",
)
READ_TIMEOUT = 25.0
READ_PAUSE = 0.25
MAX_DURATION = 120.0
MAX_CONSECUTIVE_FAILURES = 3
IDENTITY_PROPERTIES = frozenset(SOUL_PROPERTIES[-4:])


def supported(device: EcamDevice) -> bool:
    return device.profile.family == "ecam61x"


class RecipeReader:
    """One explicit run at a time; no timer, wake, session or brew commands."""

    def __init__(self, device: EcamDevice, changed: Callable[[], None]) -> None:
        self.device = device
        self._changed = changed
        self.task: asyncio.Task[None] | None = None
        self.running = False
        self.started_at: str | None = None
        self.finished_at: str | None = None
        self.result = "not_started"
        self.stop_reason: str | None = None
        self.attempted = 0
        self.received = 0
        self.valid_catalog_entries = 0
        self.errors: list[dict[str, str]] = []

    def snapshot(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "result": self.result,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "stop_reason": self.stop_reason,
            "total": len(SOUL_PROPERTIES),
            "attempted": self.attempted,
            "received": self.received,
            "valid_catalog_entries": self.valid_catalog_entries,
            "errors": [dict(error) for error in self.errors],
            "max_duration_seconds": MAX_DURATION,
            "read_timeout_seconds": READ_TIMEOUT,
            "read_pause_seconds": READ_PAUSE,
        }

    def start(self, hass: Any, entry: Any) -> bool:
        if self.running:
            return False
        if (
            not supported(self.device)
            or not self.device.available
            or self.device.is_busy()
        ):
            raise ValueError(
                "recipe reads require an available, idle Soul-family machine"
            )
        self.running = True
        self.result = "running"
        self.stop_reason = None
        self.started_at = datetime.now(UTC).isoformat()
        self.finished_at = None
        self.attempted = self.received = self.valid_catalog_entries = 0
        self.errors = []
        self.task = entry.async_create_background_task(
            hass, self._read(), "cremalink recipe read"
        )
        self._changed()
        return True

    async def _read(self) -> None:
        deadline = time.monotonic() + MAX_DURATION
        failures = 0
        try:
            for name in SOUL_PROPERTIES:
                if not self.device.available or self.device.is_busy():
                    self.stop_reason = "machine_unavailable_or_busy"
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self.stop_reason = "time_limit"
                    break
                self.attempted += 1
                try:
                    async with asyncio.timeout(min(READ_TIMEOUT, remaining)):
                        await self.device.link.read(
                            name, timeout=min(READ_TIMEOUT, remaining)
                        )
                except (EcamError, TimeoutError) as err:
                    failures += 1
                    self.errors.append({"property": name, "error": type(err).__name__})
                    if failures >= MAX_CONSECUTIVE_FAILURES:
                        self.stop_reason = "consecutive_read_failures"
                        break
                else:
                    failures = 0
                    self.received += 1
                    if name not in IDENTITY_PROPERTIES:
                        item = next(
                            (
                                e
                                for e in self.device.catalog.snapshot()["entries"]
                                if e["property"] == name
                            ),
                            None,
                        )
                        if item and item["last_error"] is None:
                            self.valid_catalog_entries += 1
                        else:
                            self.errors.append(
                                {"property": name, "error": "invalid_catalog_value"}
                            )
                self._changed()
                await asyncio.sleep(
                    min(READ_PAUSE, max(0.0, deadline - time.monotonic()))
                )
            self.result = "partial" if self.stop_reason or self.errors else "completed"
        except asyncio.CancelledError:
            self.result = "cancelled"
            self.stop_reason = "entry_unloaded_or_shutdown"
            raise
        except Exception as err:
            self.result = "failed"
            # Never expose raw values or exception text in the export.
            self.stop_reason = type(err).__name__
        finally:
            self.running = False
            self.finished_at = datetime.now(UTC).isoformat()
            self._changed()
