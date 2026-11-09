"""Standalone, monitor-only user service supervisor.

Designed for an optional systemd --user unit. OFF performs no monitoring;
MONITOR receives privacy-minimal metadata. No suspend, power profile change,
inhibitor or privileged action can be dispatched from this module.
"""
from __future__ import annotations

import time

from .receiver import read_monitor_health
from .service import MonitorUnavailable, serve_monitor
from .mode_gate import current_mode


def monitor_service_loop(
    *, max_iterations: int | None = None,
    idle_seconds: float = 1.0,
) -> int:
    """Independent of GTK; retry conservatively with bounded backoff.

    A private, healthy receiver started manually is left untouched. When that
    receiver exits, this service can take over. Missing configuration fails
    closed (OFF), never interpreted as permission to suspend.
    """
    if max_iterations is not None and (
        type(max_iterations) is not int or not 0 <= max_iterations <= 100000
    ):
        raise ValueError("invalid_iteration_bound")
    failures = 0
    attempts = 0
    while max_iterations is None or attempts < max_iterations:
        attempts += 1
        if not current_mode().monitor_allowed:
            failures = 0
            time.sleep(idle_seconds)
            continue
        if read_monitor_health() is not None:
            # A live local owner exists; do not displace it.
            failures = 0
            time.sleep(idle_seconds)
            continue
        try:
            outcome = serve_monitor(max_seconds=0, quiet=True)
            if outcome.reason in ("mode_off", "interrupted"):
                failures = 0
            else:
                failures += 1
        except (MonitorUnavailable, OSError):
            failures += 1
        # Backoff avoids hot-restarting a broken monitor or bus session.
        time.sleep(min(60.0, idle_seconds * (2 ** min(failures, 6))))
    return 0
