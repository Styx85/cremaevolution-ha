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
    """One explicit part at a time; no timer, wake, session or brew commands.

    A press reads one bounded part of a *pass* over ``SOUL_PROPERTIES``. The
    next press continues the pass with the properties not yet confirmed:
    never-tried ones first, then ones that failed. A property counts as
    confirmed only after a successful read (catalogue properties also need a
    valid catalogue entry) or when a valid answer arrives late, after the pass
    started. Once every property is confirmed the pass is complete and the
    next press starts a fresh pass. The pass lives in memory only: a restart
    or reload of the entry starts over.
    """

    def __init__(self, device: EcamDevice, changed: Callable[[], None]) -> None:
        self.device = device
        self._changed = changed
        self.task: asyncio.Task[None] | None = None
        self.running = False
        # current part
        self.started_at: str | None = None
        self.finished_at: str | None = None
        self.result = "not_started"
        self.stop_reason: str | None = None
        self.attempted = 0
        self.received = 0
        self.valid_catalog_entries = 0
        self.errors: list[dict[str, str]] = []
        # current pass
        self.pass_started_at: str | None = None
        self.pass_finished_at: str | None = None
        self._pass_start_ts = 0.0
        self.parts = 0
        self.confirmed: set[str] = set()
        self.late: set[str] = set()
        self.failed: set[str] = set()

    @property
    def pass_complete(self) -> bool:
        return self.pass_started_at is not None and not self._pending()

    def _pending(self) -> list[str]:
        """Unconfirmed properties: never tried first, then failed ones."""
        open_ = [name for name in SOUL_PROPERTIES if name not in self.confirmed]
        return [n for n in open_ if n not in self.failed] + [n for n in open_ if n in self.failed]

    def _valid_entry(self, name: str) -> dict[str, Any] | None:
        return next(
            (
                e
                for e in self.device.catalog.snapshot()["entries"]
                if e["property"] == name and e["last_error"] is None
            ),
            None,
        )

    def _collect_late_answers(self) -> None:
        """Count valid catalogue answers that arrived after their read timed out."""
        if self.pass_started_at is None:
            return
        for entry in self.device.catalog.snapshot()["entries"]:
            name = entry["property"]
            if (
                name in self.failed
                and name not in self.confirmed
                and entry["last_error"] is None
                and entry.get("received_at", 0) >= self._pass_start_ts
            ):
                self.confirmed.add(name)
                self.failed.discard(name)
                self.late.add(name)
        if not self._pending() and self.pass_finished_at is None:
            self.pass_finished_at = datetime.now(UTC).isoformat()

    def snapshot(self) -> dict[str, Any]:
        self._collect_late_answers()
        pending = self._pending() if self.pass_started_at else list(SOUL_PROPERTIES)
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
            "pass": {
                "started_at": self.pass_started_at,
                "finished_at": self.pass_finished_at,
                "complete": self.pass_complete,
                "parts": self.parts,
                "confirmed": len(self.confirmed),
                "late_answers": sorted(self.late),
                "failed_pending": sorted(n for n in self.failed if n not in self.confirmed),
                "pending": len(pending),
                "next_property": pending[0] if pending else None,
                "memory_only": True,
            },
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
        self._collect_late_answers()
        if self.pass_started_at is None or self.pass_complete:
            self.pass_started_at = datetime.now(UTC).isoformat()
            self.pass_finished_at = None
            self._pass_start_ts = time.time()
            self.parts = 0
            self.confirmed = set()
            self.late = set()
            self.failed = set()
        self.parts += 1
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
            for name in self._pending():
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
                    self.failed.add(name)
                    self.errors.append({"property": name, "error": type(err).__name__})
                    if failures >= MAX_CONSECUTIVE_FAILURES:
                        self.stop_reason = "consecutive_read_failures"
                        break
                else:
                    failures = 0
                    self.received += 1
                    if name in IDENTITY_PROPERTIES:
                        self.confirmed.add(name)
                        self.failed.discard(name)
                    elif self._valid_entry(name):
                        self.valid_catalog_entries += 1
                        self.confirmed.add(name)
                        self.failed.discard(name)
                    else:
                        # answered, but not a valid catalogue value: stays pending
                        self.failed.add(name)
                        self.errors.append(
                            {"property": name, "error": "invalid_catalog_value"}
                        )
                self._changed()
                await asyncio.sleep(
                    min(READ_PAUSE, max(0.0, deadline - time.monotonic()))
                )
            self._collect_late_answers()
            if self.pass_complete:
                self.result = "completed"
                self.pass_finished_at = self.pass_finished_at or datetime.now(UTC).isoformat()
            else:
                self.result = "partial"
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
