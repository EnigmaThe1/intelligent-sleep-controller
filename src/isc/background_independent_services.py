"""MONITOR-only periodic discovery of active systemd services.

Produces aggregate counts from both system and user scopes, without retaining
service names, invoking power controls or making task-completion claims.
Missing observations reset comparison history; count changes are *net count
deltas*, not identifiable service starts/stops or verified work.
"""
from __future__ import annotations

from dataclasses import dataclass
from threading import Event, Thread
from typing import Callable
import math
import time

from .independent_service_discovery import (
    IndependentServiceCounts, read_independent_services,
)

_MAX = 2**31


@dataclass(frozen=True)
class IndependentServicePulse:
    active_services: int | None
    other_active: int | None
    timer_linked_active: int | None
    net_count_delta: int | None
    observed_at: float
    coverage_verified: bool = False


class BackgroundIndependentServiceObserver:
    """Best-effort read-only discovery; failure means UNKNOWN, never idle."""

    def __init__(
        self,
        publish: Callable[[IndependentServicePulse], None],
        *, interval_seconds: float = 90.0,
        scanner: Callable[..., IndependentServiceCounts] = read_independent_services,
    ) -> None:
        if (
            not callable(publish) or not callable(scanner)
            or type(interval_seconds) not in (int, float)
            or not math.isfinite(interval_seconds)
            or not 10 <= interval_seconds <= 300
        ):
            raise ValueError("invalid_independent_service_observer")
        self._publish = publish
        self._scanner = scanner
        self._interval = float(interval_seconds)
        self._stop = Event()
        self._thread: Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("independent_service_observer_already_started")
        self._thread = Thread(
            target=self._run, daemon=True, name="isc-independent-services",
        )
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)

    @staticmethod
    def _valid(value: object) -> bool:
        return (
            isinstance(value, IndependentServiceCounts)
            and value.uncertain is False
            and value.coverage_verified is False
            and value.task_owner_authenticated is False
            and all(
                type(count) is int and 0 <= count <= _MAX
                for count in (
                    value.active_services, value.other_active,
                    value.timer_linked_active,
                )
            )
            and value.other_active + value.timer_linked_active
            == value.active_services
        )

    def _run(self) -> None:
        previous: tuple[int, int, int] | None = None
        while not self._stop.is_set():
            try:
                system = self._scanner(scope="--system")
                user = self._scanner(scope="--user")
            except Exception:
                system = user = None
            now = time.monotonic()
            if not self._valid(system) or not self._valid(user):
                previous = None
                pulse = IndependentServicePulse(None, None, None, None, now)
            else:
                current = (
                    system.active_services + user.active_services,
                    system.other_active + user.other_active,
                    system.timer_linked_active + user.timer_linked_active,
                )
                if any(count > _MAX for count in current):
                    previous = None
                    pulse = IndependentServicePulse(None, None, None, None, now)
                else:
                    # A net change is not the count of identities changing.
                    delta = (
                        sum(abs(a - b) for a, b in zip(previous, current))
                        if previous is not None else None
                    )
                    pulse = IndependentServicePulse(
                        current[0], current[1], current[2], delta, now,
                    )
                    previous = current
            try:
                self._publish(pulse)
            except Exception:
                # No pulse is safer than blocking MONITOR's hook receiver.
                pass
            self._stop.wait(self._interval)
