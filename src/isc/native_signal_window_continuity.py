"""Step 99h: bounded, in-memory continuity diagnostics for native signal windows.

This is a pure MONITOR-only classifier, not an event listener or a source
of suspend eligibility. Short gaps between observations remain blind;
successful consecutive windows NEVER establish uninterrupted work coverage.
No D-Bus names, service paths, PIDs or event payloads are stored.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

from .native_systemd_bracketed_probe import BracketedSignalSummary

_MAX_WINDOWS = 256
_MAX_DURATION = 30.0
_MAX_GAP = 60.0
_ALLOWED = frozenset(("QUIET_UNVERIFIED", "SENDER_MATCH_UNVERIFIED"))


@dataclass(frozen=True)
class WindowContinuityPulse:
    state: str
    windows: int
    last_observed_at: float | None
    blind_gap_seconds: float | None = None
    coverage_verified: bool = False
    uninterrupted_events_verified: bool = False
    task_owner_authenticated: bool = False


class NativeWindowContinuity:
    """Explicitly enrolled sequence of limited, non-authoritative windows.

    A failed/changed source, missing observation, excessive gap, clock
    regression or capacity overflow becomes sticky UNKNOWN. The instance
    must not be silently reset to make older missed work appear resolved.
    """

    def __init__(self) -> None:
        self._lost = False
        self._windows = 0
        self._last_end: float | None = None
        self._blind_gap: float | None = None

    @staticmethod
    def _time(value: object) -> bool:
        return type(value) in (int, float) and math.isfinite(value) and value >= 0

    def observe(
        self, result: BracketedSignalSummary | None, *,
        started_at: float, ended_at: float,
    ) -> WindowContinuityPulse:
        if self._lost:
            return self.summary()
        if (not self._time(started_at) or not self._time(ended_at)
                or ended_at < started_at
                or ended_at - started_at > _MAX_DURATION
                or self._windows >= _MAX_WINDOWS
                or not isinstance(result, BracketedSignalSummary)
                or result.status not in _ALLOWED
                or result.coverage_verified is not False
                or result.event_delivery_verified is not False
                or result.task_owner_authenticated is not False
                or result.manager_sender_correlated is not
                (result.status == "SENDER_MATCH_UNVERIFIED")
                or (result.status == "QUIET_UNVERIFIED" and
                    result.observed_candidates is not None)
                or (result.status == "SENDER_MATCH_UNVERIFIED" and
                    result.observed_candidates != 1)):
            self._lost = True
            return self.summary()
        start = float(started_at)
        end = float(ended_at)
        if self._last_end is not None:
            gap = start - self._last_end
            if gap < 0 or gap > _MAX_GAP:
                self._lost = True
                return self.summary()
            # Every nonzero gap is a real unobserved interval, not coverage.
            self._blind_gap = gap
        self._last_end = end
        self._windows += 1
        return self.summary()

    def invalidate(self) -> WindowContinuityPulse:
        """Explicit transport-loss notification; sticky until new enrollment."""
        self._lost = True
        return self.summary()

    def summary(self) -> WindowContinuityPulse:
        return WindowContinuityPulse(
            state="UNKNOWN" if self._lost else (
                "WINDOWS_UNVERIFIED" if self._windows else "NOT_OBSERVED"),
            windows=self._windows,
            last_observed_at=self._last_end,
            blind_gap_seconds=self._blind_gap,
        )
