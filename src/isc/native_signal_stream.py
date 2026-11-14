"""Pure Step 98 systemd signal-stream integrity model (MONITOR only).

No D-Bus connection or subscription is made here. Inputs are synthetic,
aggregate-only notification types supplied by a future scoped adapter.
Observing a unit/job notification NEVER proves application completion,
scheduler completeness, or authorization to suspend.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

_EVENTS = frozenset(("JobNew", "JobRemoved", "UnitNew", "UnitRemoved", "PropertiesChanged"))
_MAX_EVENTS = 256
_MAX_GAP_SECONDS = 60.0


@dataclass(frozen=True)
class NativeSignalPulse:
    status: str
    events: int | None
    interruptions: int
    observed_at: float | None
    coverage_verified: bool = False
    uninterrupted_events_verified: bool = False
    task_owner_authenticated: bool = False


class NativeSignalStream:
    """Bounded, opt-in in-process signal diagnostics; never grants authority.

    A successful connection is only evidence that a caller reported being
    connected. Event delivery is never guaranteed even within an interval.
    Any lost connection, invalid event, clock reversal or excessive gap
    permanently marks this observer UNKNOWN until a new observer is created.
    """

    def __init__(self) -> None:
        self._connected = False
        self._lost = False
        self._events = 0
        self._interruptions = 0
        self._last_time: float | None = None

    @staticmethod
    def _valid_time(now: object) -> bool:
        return type(now) in (int, float) and math.isfinite(now) and now >= 0

    def _advance(self, now: object) -> bool:
        if not self._valid_time(now):
            self._invalidate()
            return False
        now = float(now)
        if self._last_time is not None and (
            now < self._last_time or now - self._last_time > _MAX_GAP_SECONDS
        ):
            self._invalidate()
            return False
        self._last_time = now
        return True

    def _invalidate(self) -> None:
        if not self._lost:
            self._interruptions += 1
        self._lost = True
        self._connected = False

    def connect(self, *, now: float) -> bool:
        if not self._advance(now) or self._lost or self._connected:
            self._invalidate()
            return False
        self._connected = True
        return True

    def event(self, name: object, *, now: float) -> bool:
        if not self._advance(now):
            return False
        if (self._lost or not self._connected or not isinstance(name, str)
                or name not in _EVENTS or self._events >= _MAX_EVENTS):
            self._invalidate()
            return False
        self._events += 1
        return True

    def heartbeat(self, *, now: float) -> bool:
        """A heartbeat checks polling freshness, not signal delivery."""
        if not self._advance(now):
            return False
        if self._lost or not self._connected:
            self._invalidate()
            return False
        return True

    def disconnect(self) -> None:
        self._invalidate()

    def summary(self) -> NativeSignalPulse:
        if self._lost:
            state = "UNKNOWN"
            count = None
        elif not self._connected:
            state = "NOT_CONNECTED"
            count = None
        else:
            state = "OBSERVING_UNVERIFIED"
            count = self._events
        return NativeSignalPulse(
            status=state, events=count, interruptions=self._interruptions,
            observed_at=self._last_time,
        )
