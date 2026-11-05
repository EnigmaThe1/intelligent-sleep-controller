"""Operator-invoked Linux suspend, independent of automatic ISC modes.

Preview is read-only. Real suspend requires a local interactive terminal,
explicit --confirm, a second typed confirmation and fresh OS preflight.
Never overrides inhibitors, runs a shell, modifies GNOME, or executes in CI.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
import shlex
import subprocess
import sys

from .inhibitors import read_inhibitors
from .processes import read_process_candidates

_CANSUSPEND_COMMAND = (
    "busctl", "--system", "call",
    "org.freedesktop.login1", "/org/freedesktop/login1",
    "org.freedesktop.login1.Manager", "CanSuspend",
)
_SUSPEND_COMMAND = ("systemctl", "--check-inhibitors=yes", "suspend")


@dataclass(frozen=True)
class SuspendAssessment:
    request_possible: bool
    capability: str
    blockers: tuple[str, ...]
    candidate_processes: int | None
    workload_coverage_verified: bool = False


@dataclass(frozen=True)
class ManualSuspendResult:
    submitted: bool
    reason: str


def can_suspend(*, timeout_seconds: float = 3.0) -> str:
    """Read-only logind capability probe. No auth or power operation."""
    try:
        result = subprocess.run(
            list(_CANSUSPEND_COMMAND),
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout_seconds,
        )
        if result.returncode:
            return "unknown"
        tokens = shlex.split(result.stdout)
        if len(tokens) == 2 and tokens[0] == "s" and tokens[1] in (
            "yes", "challenge", "no", "na",
        ):
            return tokens[1]
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return "unknown"


def assess_manual_suspend() -> SuspendAssessment:
    """Check OS capability/inhibitors and note incomplete workload coverage.

    Unlike AUTO sleep, manual user intent may override unverified workload
    coverage after an unmistakable interactive confirmation. The caller must
    understand that hook/process observation is incomplete.
    """
    capability = can_suspend()
    blockers: list[str] = []
    if capability not in ("yes", "challenge"):
        blockers.append("suspend_not_confirmed_available")
    locks = read_inhibitors()
    if locks is None:
        blockers.append("sleep_inhibitors_unverified")
    elif any(lock.blocks_automatic_sleep for lock in locks):
        blockers.append("blocking_sleep_inhibitor_present")
    scan = read_process_candidates()
    if scan is None:
        blockers.append("process_inventory_unverified")
    return SuspendAssessment(
        request_possible=not blockers,
        capability=capability,
        blockers=tuple(blockers),
        candidate_processes=scan.count if scan is not None else None,
    )


def request_manual_suspend(*, confirmed: bool = False) -> ManualSuspendResult:
    """A real suspend is requested ONLY by an explicit local human action.

    No automatic-mode dependency: OFF means *automatic* suspension is off,
    not that the user is prevented from manually suspending Linux.
    """
    if not confirmed:
        return ManualSuspendResult(False, "confirmation_flag_required")
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return ManualSuspendResult(False, "interactive_terminal_required")
    initial = assess_manual_suspend()
    if not initial.request_possible:
        return ManualSuspendResult(False, "preflight_blocked")
    print(
        "WARNING: ISC cannot verify every running or background task. "
        "Manual suspend can interrupt active work."
    )
    print(
        "Known candidate processes (activity UNKNOWN): "
        + str(initial.candidate_processes)
    )
    print("Your machine may also have display-resume issues.")
    try:
        typed = input("Type SUSPEND to request immediate system sleep: ")
    except (EOFError, KeyboardInterrupt):
        return ManualSuspendResult(False, "cancelled")
    if typed != "SUSPEND":
        return ManualSuspendResult(False, "cancelled")
    return _submit_after_preflight()


def request_dialog_suspend(*, confirmed: bool = False, typed: str = "") -> ManualSuspendResult:
    """Manual GUI action requires an explicit typed phrase and fresh checks."""
    if not confirmed or typed != "SUSPEND":
        return ManualSuspendResult(False, "dialog_confirmation_required")
    if not assess_manual_suspend().request_possible:
        return ManualSuspendResult(False, "preflight_blocked")
    return _submit_after_preflight()


def _submit_after_preflight() -> ManualSuspendResult:
    """Last-moment recheck, then request systemd suspend with inhibitors."""
    if not assess_manual_suspend().request_possible:
        return ManualSuspendResult(False, "preflight_changed_or_blocked")
    try:
        result = subprocess.run(
            list(_SUSPEND_COMMAND),
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ManualSuspendResult(False, "system_suspend_request_unavailable")
    if result.returncode:
        return ManualSuspendResult(False, "system_suspend_request_rejected")
    return ManualSuspendResult(True, "request_accepted_by_systemd")
