"""Operator-controlled native sleep guard for an already-running user service.

Does NOT attach to, reconfigure, restart or stop the service. Instead a
separate systemd-inhibit process holds a sleep/idle lock for the duration
of this foreground guard, while read-only process checks continue.

The guard does not exit merely because a service disappears: process exit
is not application task completion (detached children may survive). The
operator releases the lock with Ctrl+C. A crash of the guard/inhibitor may
release the lock unexpectedly; this is not automatic ON coverage. ON remains
disarmed until an independent workload and wake authority exists.
"""
from __future__ import annotations

import math
import os
import sys
import time
from typing import Callable

from .existing_service_check import (
    ExistingServiceObservation, observe_existing_user_service,
)
from .protected_work import _SYSTEMD_INHIBIT, protected_command
from .service_process_correlation import _SERVICE

_POLL_SECONDS = 30.0
_MAX_DURATION_SECONDS = 14400


def valid_guard_duration(value: object) -> bool:
    """None means manual cancellation; otherwise a bounded explicit lease."""
    return value is None or (
        type(value) is int and 1 <= value <= _MAX_DURATION_SECONDS
    )


def _unit_is_valid(unit: object) -> bool:
    return (type(unit) is str and _SERVICE.fullmatch(unit) is not None)


def existing_service_guard_command(
    unit: object, *,
    python_executable: str | None = None,
    duration_seconds: int | None = None,
) -> tuple[str, ...] | None:
    """Create native inhibitor command, never a shell or service mutation."""
    if not _unit_is_valid(unit) or not valid_guard_duration(duration_seconds):
        return None
    interpreter = sys.executable if python_executable is None else python_executable
    if (
        type(interpreter) is not str or not os.path.isabs(interpreter)
        or not interpreter or "\x00" in interpreter
    ):
        return None
    worker = (interpreter, "-m", "isc", "_existing-guard-worker", unit)
    if duration_seconds is not None:
        worker += (str(duration_seconds),)
    return protected_command(worker)


def start_existing_service_guard(
    unit: object,
    *,
    duration_seconds: int | None = None,
    probe: Callable[[str], ExistingServiceObservation] = (
        observe_existing_user_service
    ),
) -> int:
    """Explicitly protect one *verified* current-user service.

    A fresh native membership preflight must succeed before starting the
    separate inhibitor. If the source is unavailable, do not run a guard
    for a service that may not exist; never treat that refusal as sleep-safe.
    """
    command = existing_service_guard_command(
        unit, duration_seconds=duration_seconds,
    )
    if command is None or not callable(probe):
        return 2
    try:
        current = probe(unit)
        if (
            not isinstance(current, ExistingServiceObservation)
            or current.verified_service_membership is not True
            or current.workload_coverage_complete is not False
            or current.task_activity_verified is not False
            or type(current.observed_processes) is not int
            or current.observed_processes <= 0
        ):
            return 2
        os.execv(_SYSTEMD_INHIBIT, command)
    except (OSError, ValueError, TypeError, UnicodeError, RuntimeError):
        return 2
    return 2


def run_existing_guard_worker(
    unit: object, *,
    duration_seconds: int | None = None,
    probe: Callable[[str], ExistingServiceObservation] = (
        observe_existing_user_service
    ),
    wait: Callable[[float], object] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    """Observe only; native systemd-inhibit owns the actual lock.

    This worker never issues a native inhibitor command. It intentionally
    does not exit on service stop, CPU inactivity, missing procfs evidence
    or negative scan results: no such signal proves task completion.
    """
    if (
        not _unit_is_valid(unit) or not valid_guard_duration(duration_seconds)
        or not callable(probe) or not callable(wait) or not callable(clock)
    ):
        return 2
    if duration_seconds is not None:
        try:
            started = clock()
            if (type(started) not in (int, float)
                    or not math.isfinite(started) or started < 0):
                return 2
        except (OSError, ValueError, TypeError, RuntimeError):
            return 2
        deadline = started + duration_seconds
    else:
        deadline = None

    print("ISC EXISTING USER SERVICE GUARD")
    print("Read-only observation running. Operator cancellation: Ctrl+C.")
    print("Missing service evidence does NOT establish safe completion.")
    previous: str | None = None
    try:
        while True:
            try:
                observed = probe(unit)
                if (
                    isinstance(observed, ExistingServiceObservation)
                    and observed.verified_service_membership is True
                    and observed.workload_coverage_complete is False
                    and observed.task_activity_verified is False
                    and type(observed.observed_processes) is int
                    and observed.observed_processes > 0
                ):
                    state = "SERVICE MEMBERSHIP OBSERVED; TASK STATE UNKNOWN"
                else:
                    state = "SERVICE/WORKLOAD UNKNOWN; KEEP PROTECTION"
            except (OSError, ValueError, TypeError, RuntimeError):
                state = "SERVICE/WORKLOAD UNKNOWN; KEEP PROTECTION"
            if state != previous:
                print(state, flush=True)
                previous = state
            if deadline is None:
                wait(_POLL_SECONDS)
            else:
                now = clock()
                if (
                    type(now) not in (int, float)
                    or not math.isfinite(now) or now < started
                ):
                    # Clock failure is not proof of completed work.
                    print("Guard clock unavailable; task state UNKNOWN.")
                    return 2
                remaining = deadline - now
                if remaining <= 0:
                    print(
                        "Selected protection period ended; task state UNKNOWN.",
                        flush=True,
                    )
                    return 0
                wait(min(_POLL_SECONDS, remaining))
    except KeyboardInterrupt:
        print("Guard cancelled by operator; surrounding inhibitor can exit.")
        return 0
    except (OSError, ValueError, RuntimeError, TypeError):
        # A failed observer must never report that work is complete.
        print("Guard interrupted by observer failure; task state UNKNOWN.")
        return 2
