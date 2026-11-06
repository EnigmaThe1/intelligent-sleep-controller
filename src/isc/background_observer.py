"""Independent, read-only periodic Linux process observations.

Never authorises suspend, claims complete process coverage, or reads private
process command lines, environments, files, or network data. Polling runs in
one daemon thread to avoid blocking the MONITOR hook receiver.
"""
from __future__ import annotations

from dataclasses import dataclass
from threading import Event, Thread
from typing import Callable
import math
import time

from .process_lifecycle import ProcessLineage, compare_process_lineage, read_process_lineage

DEFAULT_INTERVAL_SECONDS = 30.0
MAX_OBSERVED_COUNT = 2**31


@dataclass(frozen=True)
class ProcessPulse:
    roots: int | None
    descendants: int | None
    visibility_races: int | None
    identity_changes: int | None
    observed_at: float
    # A partial name-based census can NEVER prove overall workload coverage.
    coverage_verified: bool = False


class BackgroundProcessObserver:
    """Best-effort continuous observation; failures become UNKNOWN, not zero."""

    def __init__(
        self,
        publish: Callable[[ProcessPulse], None],
        *,
        interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
        scanner: Callable[[], ProcessLineage | None] = read_process_lineage,
    ) -> None:
        if (
            not callable(publish) or not callable(scanner)
            or not isinstance(interval_seconds, (int, float))
            or not math.isfinite(interval_seconds)
            or not 1 <= interval_seconds <= 300
        ):
            raise ValueError("invalid_process_observer")
        self._publish = publish
        self._scanner = scanner
        self._interval = float(interval_seconds)
        self._stop = Event()
        self._thread: Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("observer_already_started")
        self._thread = Thread(
            target=self._run, name="isc-process-observer", daemon=True,
        )
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            # A slow process-table snapshot may still be finishing; daemon
            # lifetime cannot prevent MONITOR service shutdown.
            thread.join(timeout=1.0)

    def __enter__(self) -> BackgroundProcessObserver:
        self.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _run(self) -> None:
        previous: ProcessLineage | None = None
        observed_changes = 0
        observed_races = 0
        while not self._stop.is_set():
            try:
                current = self._scanner()
            except Exception:
                # The observer is not an authority. A scanner failure is
                # explicitly unknown and cannot make sleep eligible.
                current = None
            now = time.monotonic()
            if current is None:
                previous = None
                pulse = ProcessPulse(
                    roots=None, descendants=None,
                    visibility_races=None, identity_changes=None,
                    observed_at=now,
                )
            else:
                observed_races = min(
                    MAX_OBSERVED_COUNT,
                    observed_races + current.vanished_during_scan,
                )
                change = compare_process_lineage(previous, current)
                if change is not None:
                    observed_changes = min(
                        MAX_OBSERVED_COUNT,
                        observed_changes
                        + change.appeared + change.disappeared,
                    )
                previous = current
                pulse = ProcessPulse(
                    roots=len(current.roots),
                    descendants=len(current.descendants),
                    visibility_races=observed_races,
                    identity_changes=observed_changes,
                    observed_at=now,
                )
            try:
                self._publish(pulse)
            except Exception:
                # Never crash the service or affect another application.
                # No published observation = UNKNOWN in the receiver.
                pass
            self._stop.wait(self._interval)
