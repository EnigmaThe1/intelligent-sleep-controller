"""Native automatic-suspend capability and inhibitor preflight (read-only).

This is not workload coverage and never authorizes a power action alone.
It does not reuse the manual-suspend exception for partially observed work.
A challenge response cannot be approved unattended. Every malformed or
unavailable source fails closed. No service, GNOME setting or power state
is changed by this module.
"""
from __future__ import annotations

from dataclasses import dataclass
import subprocess

from .host_safety import read_host_snapshot
from .inhibitors import Inhibitor, read_inhibitors
from .manual_power import can_suspend
from .mode_gate import current_mode
from .policy import Snapshot, evaluate


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


# The only native automatic-power command. This is not used by manual sleep.
_AUTO_COMMAND = ("systemctl", "--check-inhibitors=yes", "suspend")


@dataclass(frozen=True)
class AutoNativeSubmitResult:
    submitted: bool
    reason: str


def submit_automatic_native() -> AutoNativeSubmitResult:
    """Recheck the real saved mode and safety evidence before systemctl.

    No injectable readers: an artificial ON from a unit test must never
    grant real power authority. The current release disarms ON in settings
    and mode_gate. Transition serialization, hardware validation and trusted
    workload coverage must be completed *before* ON is ever enabled.
    """
    try:
        mode = current_mode()
        if (mode.mode != "on" or mode.configuration_valid is not True
                or mode.automatic_suspend_allowed is not True):
            return AutoNativeSubmitResult(False, "automatic_suspend_disarmed")

        # A native capability check is necessary but cannot authenticate work.
        snapshot = read_host_snapshot()
        if not isinstance(snapshot, Snapshot) or not evaluate(snapshot).eligible:
            return AutoNativeSubmitResult(False, "final_workload_check_blocked")

        native = assess_auto_native_suspend()
        if (not isinstance(native, AutoNativeAssessment)
                or native.ready is not True or native.capability != "yes"
                or native.blockers):
            return AutoNativeSubmitResult(False, "final_native_check_blocked")

        # A late OFF transition or a newly active job must abort submission.
        if current_mode() != mode:
            return AutoNativeSubmitResult(False, "operator_mode_changed")
        latest = read_host_snapshot()
        if not isinstance(latest, Snapshot) or not evaluate(latest).eligible:
            return AutoNativeSubmitResult(False, "last_workload_check_blocked")

        # logind rechecks inhibitors; this does NOT solve all TOCTOU races.
        command = subprocess.run(
            _AUTO_COMMAND, capture_output=True, text=True,
            check=False, timeout=20,
        )
        if command.returncode != 0:
            return AutoNativeSubmitResult(False, "native_suspend_rejected")
        return AutoNativeSubmitResult(True, "request_accepted_by_systemd")
    except (OSError, ValueError, RuntimeError, TypeError, subprocess.TimeoutExpired):
        return AutoNativeSubmitResult(False, "native_suspend_unavailable")
