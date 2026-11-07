"""Best-effort MONITOR-only periodic timer-linked service observation.

Only aggregate transition counts leave the worker. Neither unchanged state
nor task disappearance proves completion. Failure and stale samples remain
UNKNOWN, and the observer never starts or stops services.
"""
from __future__ import annotations

from dataclasses import dataclass
from threading import Event, Thread
from typing import Callable
import math
import time

from .timer_service_lifecycle import (
    ServiceTaskSnapshot, compare_service_task_snapshots,
    read_service_task_snapshots,
)


@dataclass(frozen=True)
class SchedulerPulse:
    services: int | None
    with_tasks: int | None
    uncertain: int | None
    transitions: int | None
    observed_at: float
    coverage_verified: bool = False
    uncertain_transitions: int | None = None


class BackgroundSchedulerObserver:
    def __init__(
        self,
        publish: Callable[[SchedulerPulse], None],
        *, interval_seconds: float = 60.0,
        scanner: Callable[[], tuple[
            ServiceTaskSnapshot | None, ServiceTaskSnapshot | None,
        ]] = read_service_task_snapshots,
    ) -> None:
        if (
            not callable(publish) or not callable(scanner)
            or not isinstance(interval_seconds, (int, float))
            or not math.isfinite(interval_seconds)
            or not 5 <= interval_seconds <= 300
        ):
            raise ValueError("invalid_scheduler_observer")
        self._publish = publish
        self._scanner = scanner
        self._interval = float(interval_seconds)
        self._stop = Event()
        self._thread: Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("scheduler_observer_already_started")
        self._thread = Thread(
            target=self._run, name="isc-scheduler-observer", daemon=True,
        )
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)

    def _run(self) -> None:
        previous: tuple[
            ServiceTaskSnapshot | None, ServiceTaskSnapshot | None,
        ] = (None, None)
        while not self._stop.is_set():
            try:
                current = self._scanner()
                if not isinstance(current, tuple) or len(current) != 2:
                    current = (None, None)
            except Exception:
                current = (None, None)
            now = time.monotonic()
            if any(scope is None for scope in current):
                pulse = SchedulerPulse(
                    None, None, None, None, now, uncertain_transitions=None,
                )
                previous = (None, None)
            else:
                # Never retain raw names beyond the previous sampling interval.
                changes = [
                    compare_service_task_snapshots(a, b)
                    for a, b in zip(previous, current)
                ]
                services = sum(len(scope.observed) for scope in current if scope)
                tasks = sum(
                    sum(int(count is not None and count > 0)
                        for _, _, count in scope.observed)
                    for scope in current if scope
                )
                uncertain = sum(
                    sum(int(count is None or not group)
                        for _, group, count in scope.observed)
                    for scope in current if scope
                )
                if all(change is not None for change in changes):
                    transitions: int | None = min(2**31, sum(
                        c.appeared + c.disappeared + c.gained_tasks
                        + c.lost_reported_tasks
                        for c in changes if c is not None
                    ))
                    uncertain_transitions: int | None = min(2**31, sum(
                        c.unknown_transitions for c in changes
                        if c is not None
                    ))
                else:
                    transitions = None
                    uncertain_transitions = None
                pulse = SchedulerPulse(
                    min(services, 2**31), min(tasks, 2**31),
                    min(uncertain, 2**31), transitions, now,
                    uncertain_transitions=uncertain_transitions,
                )
                previous = current
            try:
                self._publish(pulse)
            except Exception:
                pass
            self._stop.wait(self._interval)
