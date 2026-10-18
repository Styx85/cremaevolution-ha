"""The machine state as the entities read it.

Exposes the attribute names the entities have always used (``status_name``,
``progress_percent``, ``is_busy``, flag names, ...) so unique IDs and entity
logic stay unchanged across the move from cremalink to aioecam.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from aioecam.ecam.monitor import MachineState
from aioecam.ecam.profile import Profile


@dataclass(frozen=True)
class MonitorView:
    state: MachineState
    profile: Profile
    received_at: datetime

    @property
    def status_name(self) -> str:
        return self.profile.status_name(self.state)

    @property
    def accessory_name(self) -> str:
        return self.profile.accessory_name(self.state)

    @property
    def action_name(self) -> str | None:
        return None  # action codes are not named yet

    @property
    def progress_percent(self) -> int:
        return self.state.progress

    @property
    def is_busy(self) -> bool:
        return self.profile.is_busy(self.state)

    @property
    def is_idle(self) -> bool:
        return not self.is_busy

    @property
    def available_fields(self) -> list[str]:
        """Flags this machine's profile can actually report."""
        return ["is_busy", "is_idle", *self.profile.flags]

    @property
    def parsed(self) -> dict[str, Any]:
        s = self.state
        return {
            "status": s.status,
            "action": s.action,
            "progress": s.progress,
            "accessory": s.accessory,
            "switches": list(s.switches),
            "alarms": list(s.alarms),
            "timestamp": s.timestamp,
        }

    def __getattr__(self, name: str) -> Any:
        # Profile flags (is_watertank_open, ...) as attributes. Guard against
        # lookups before the dataclass fields exist (copy/pickle).
        if name.startswith("is_") and "profile" in self.__dict__ and name in self.profile.flags:
            return self.profile.read_flags(self.state)[name]
        raise AttributeError(name)
