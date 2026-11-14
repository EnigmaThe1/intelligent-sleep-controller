"""Step 99i: opt-in bounded composition of real native observation windows.

Does not run with ISC's MONITOR service or authorize any power operation.
Repeated one-shot watchers are NOT an uninterrupted D-Bus subscription.
All outputs are aggregate-only; no bus identities, names or payloads.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import time
from typing import Callable

from .native_systemd_bracketed_probe import (
    BracketedSignalSummary, inspect_bracketed_user_manager_signal,
)
from .native_signal_window_continuity import NativeWindowContinuity


@dataclass(frozen=True)
class NativeObservationReport:
    state: str
    windows: int
    candidate_windows: int
    blind_gap_seconds: float | None
    coverage_verified: bool = False
    uninterrupted_events_verified: bool = False
    task_owner_authenticated: bool = False


def inspect_native_windows(
    *, count: int = 2, seconds: int = 3,
    observer: Callable[..., BracketedSignalSummary] = inspect_bracketed_user_manager_signal,
    clock: Callable[[], float] = time.monotonic,
) -> NativeObservationReport:
    """Run up to four bounded owner-bracketed native observations.

    Never retries a failed source: source/owner loss poisons the sequence.
    Real calls have bounded subprocess deadlines; caller-supplied injected
    observers/clocks are intended only for synthetic tests.
    """
    unknown = NativeObservationReport("UNKNOWN", 0, 0, None)
    if (type(count) is not int or not 1 <= count <= 4
            or type(seconds) is not int or not 1 <= seconds <= 10
            or not callable(observer) or not callable(clock)):
        return unknown
    continuity = NativeWindowContinuity()
    candidate_windows = 0
    for _ in range(count):
        try:
            start = clock()
            if type(start) not in (int, float) or not math.isfinite(start):
                continuity.invalidate()
                break
            sample = observer(seconds=seconds)
            end = clock()
            pulse = continuity.observe(sample, started_at=start, ended_at=end)
        except (OSError, ValueError, TypeError, RuntimeError):
            continuity.invalidate()
            break
        if pulse.state == "UNKNOWN":
            break
        if sample.status == "SENDER_MATCH_UNVERIFIED":
            candidate_windows += 1
    pulse = continuity.summary()
    return NativeObservationReport(
        state=pulse.state,
        windows=pulse.windows,
        candidate_windows=candidate_windows,
        blind_gap_seconds=pulse.blind_gap_seconds,
    )
