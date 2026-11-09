"""Native automatic-suspend capability and inhibitor preflight (read-only).

This is not workload coverage and never authorizes a power action alone.
It does not reuse the manual-suspend exception for partially observed work.
A challenge response cannot be approved unattended. Every malformed or
unavailable source fails closed. No service, GNOME setting or power state
is changed by this module.
"""
from __future__ import annotations

from dataclasses import dataclass

from .inhibitors import Inhibitor, read_inhibitors
from .manual_power import can_suspend


@dataclass(frozen=True)
class AutoNativeAssessment:
    ready: bool
    capability: str
    blockers: tuple[str, ...]


def assess_auto_native_suspend() -> AutoNativeAssessment:
    """Confirm only Linux capability and current logind inhibitor clearance.

    Workload protection remains the separate mandatory Snapshot policy;
    native readiness is never interpreted as evidence of absent work.
    Sleep/idle delay locks are conservatively treated as blockers.
    """
    reasons: list[str] = []
    try:
        capability = can_suspend()
    except (OSError, ValueError, TypeError, RuntimeError):
        capability = "unknown"
    if capability != "yes":
        reasons.append("native_suspend_capability_unverified_or_unavailable")

    try:
        locks = read_inhibitors()
    except (OSError, ValueError, TypeError, RuntimeError):
        locks = None
    if locks is None or not isinstance(locks, tuple) or any(
        not isinstance(lock, Inhibitor) or
        lock.mode not in ("block", "block-weak", "delay")
        for lock in locks
    ):
        reasons.append("logind_inhibitors_unverified")
    elif any(
        lock.blocks_automatic_sleep
        or lock.delays_sleep
        or (lock.mode == "delay" and "idle" in lock.what)
        for lock in locks
    ):
        reasons.append("logind_sleep_or_idle_inhibitor_present")

    return AutoNativeAssessment(
        ready=not reasons,
        capability=capability if type(capability) is str else "unknown",
        blockers=tuple(reasons),
    )
