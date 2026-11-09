"""Foreground MONITOR supervisor controlled by persistent OFF/MONITOR preference.

No daemon installation, service manager changes, power settings, inhibitors, or
suspend calls. Losing configuration validity or changing to OFF stops monitoring.
A lease or event in this receiver never proves workload coverage complete.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import math
import time

from .receiver import LocalMonitor
from .background_observer import BackgroundProcessObserver
from .background_scheduler import BackgroundSchedulerObserver
from .background_independent_services import BackgroundIndependentServiceObserver
from .settings import ModeConfigError, read_mode
from .mode_gate import resolve_mode


@dataclass(frozen=True)
class MonitorSummary:
    reason: str
    accepted: int = 0
    rejected: int = 0
    event_counts: tuple[tuple[str, str, int], ...] = ()


class MonitorUnavailable(RuntimeError):
    """No sensitive config details exposed in error messages."""


def _read_preference() -> str:
    """All receiver mode checks use the same disarmed, fail-closed gate."""
    try:
        effective = resolve_mode(read_mode())
    except ModeConfigError:
        raise MonitorUnavailable("invalid_or_unavailable_mode") from None
    if not effective.configuration_valid:
        raise MonitorUnavailable("invalid_or_unavailable_mode")
    return effective.mode


def serve_monitor(*, max_seconds: float = 0, poll_seconds: float = 0.5, quiet: bool = False) -> MonitorSummary:
    """Serve while MONITOR is persistently selected.

    max_seconds=0 runs indefinitely until OFF, interruption or error.
    A positive maximum is for bounded diagnosis/tests, never for sleep.
    Polling is bounded so mode changes take effect without new hook events.
    """
    if (
        not isinstance(max_seconds, (int, float))
        or not math.isfinite(max_seconds)
        or not 0 <= max_seconds <= 86400
        or not isinstance(poll_seconds, (int, float))
        or not math.isfinite(poll_seconds)
        or not 0 < poll_seconds <= 1
    ):
        raise ValueError("invalid_service_duration")
    if _read_preference() != "monitor":
        raise MonitorUnavailable("monitor_mode_not_selected")

    deadline = time.monotonic() + max_seconds if max_seconds else None
    try:
        with LocalMonitor(advertise=True) as receiver:
            observer: BackgroundProcessObserver | None = None
            scheduler: BackgroundSchedulerObserver | None = None
            independent: BackgroundIndependentServiceObserver | None = None
            if max_seconds == 0:
                candidate = BackgroundProcessObserver(
                    receiver.update_process_pulse,
                    on_snapshot=receiver.update_native_lineage,
                )
                try:
                    candidate.start()
                except (OSError, RuntimeError):
                    # Continue receiving hook events; process census UNKNOWN.
                    pass
                else:
                    observer = candidate
                candidate_scheduler = BackgroundSchedulerObserver(
                    receiver.update_scheduler_pulse,
                )
                try:
                    candidate_scheduler.start()
                except (OSError, RuntimeError):
                    # No scheduler data is authoritative; keep it UNKNOWN.
                    pass
                else:
                    scheduler = candidate_scheduler
                candidate_independent = BackgroundIndependentServiceObserver(
                    receiver.update_independent_pulse,
                )
                try:
                    candidate_independent.start()
                except (OSError, RuntimeError):
                    # Independent service census is optional and never authority.
                    pass
                else:
                    independent = candidate_independent
            try:
                return _serve_receiver(
                    receiver, deadline=deadline, poll_seconds=poll_seconds,
                    quiet=quiet,
                )
            finally:
                if observer is not None:
                    observer.close()
                if scheduler is not None:
                    scheduler.close()
                if independent is not None:
                    independent.close()
    except (OSError, PermissionError, ValueError) as exc:
        # No socket ownership, arguments or config paths in the error.
        raise MonitorUnavailable("receiver_unavailable") from None


def _serve_receiver(receiver: LocalMonitor, *, deadline: float | None,
                    poll_seconds: float, quiet: bool) -> MonitorSummary:
    """Shared MONITOR loop; no autonomous power operations."""
    if not quiet:
        print("ISC CONTROLLED FOREGROUND MONITOR (NO SUSPEND)", flush=True)
        print(f"Private Unix socket: {receiver.path}", flush=True)
        print("OFF preference stops monitoring. Ctrl+C stops foreground monitoring.", flush=True)
    reason = "time_limit"
    try:
        while True:
            if _read_preference() != "monitor":
                reason = "mode_off"
                break
            now = time.monotonic()
            remaining = None if deadline is None else deadline - now
            if remaining is not None and remaining <= 0:
                break
            interval = min(poll_seconds, remaining) if remaining is not None else poll_seconds
            receiver.run_for(interval)
    except KeyboardInterrupt:
        reason = "interrupted"
    return MonitorSummary(
        reason,
        accepted=receiver.accepted,
        rejected=receiver.rejected,
        event_counts=tuple(
            (source, event, count)
            for (source, event), count
            in sorted(receiver.event_counts.items())
        ),
    )
