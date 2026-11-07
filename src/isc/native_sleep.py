"""Guarded manual hibernate/hybrid sleep through native systemd-logind.

The machine's actual logind capability and inhibitors are authoritative.
No commands run unless the operator deliberately confirms the power action.
"""
from __future__ import annotations

from dataclasses import dataclass
import shlex
import subprocess
import sys

from .inhibitors import read_inhibitors
from .processes import read_process_candidates

_MODES = {
    "hibernate": ("CanHibernate", "HIBERNATE"),
    "hybrid-sleep": ("CanHybridSleep", "HYBRID"),
}


@dataclass(frozen=True)
class NativeSleepAssessment:
    action: str
    capability: str
    blockers: tuple[str, ...]
    candidate_processes: int | None

    @property
    def request_possible(self) -> bool:
        return not self.blockers


@dataclass(frozen=True)
class NativeSleepResult:
    submitted: bool
    reason: str


def _validate_mode(action: str) -> tuple[str, str]:
    try:
        return _MODES[action]
    except (KeyError, TypeError):
        raise ValueError("unsupported_power_action") from None


def _can_mode(action: str) -> str:
    method, _ = _validate_mode(action)
    try:
        out = subprocess.run(
            [
                "busctl", "--system", "call",
                "org.freedesktop.login1", "/org/freedesktop/login1",
                "org.freedesktop.login1.Manager", method,
            ],
            check=False, capture_output=True, text=True, timeout=3,
        )
        if out.returncode:
            return "unknown"
        parts = shlex.split(out.stdout)
        if len(parts) == 2 and parts[0] == "s" and parts[1] in (
            "yes", "challenge", "no", "na", "inhibited",
            "inhibitor-blocked", "challenge-inhibitor-blocked",
        ):
            return parts[1]
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return "unknown"


def assess_native_sleep(action: str) -> NativeSleepAssessment:
    _validate_mode(action)
    capability = _can_mode(action)
    blockers = []
    if capability not in ("yes", "challenge"):
        blockers.append("native_sleep_unavailable")
    inhibitors = read_inhibitors()
    if inhibitors is None:
        blockers.append("inhibitors_unverified")
    elif any(item.blocks_automatic_sleep for item in inhibitors):
        blockers.append("active_sleep_inhibitor")
    scan = read_process_candidates()
    if scan is None:
        blockers.append("process_inventory_unverified")
    return NativeSleepAssessment(
        action, capability, tuple(blockers),
        None if scan is None else scan.count,
    )


def _submit(action: str) -> NativeSleepResult:
    # Repeat preflight just before power action.
    if not assess_native_sleep(action).request_possible:
        return NativeSleepResult(False, "preflight_changed")
    try:
        result = subprocess.run(
            ["systemctl", "--check-inhibitors=yes", action],
            capture_output=True, text=True, check=False, timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return NativeSleepResult(False, "native_request_unavailable")
    if result.returncode:
        return NativeSleepResult(False, "native_request_refused")
    return NativeSleepResult(True, "native_request_accepted")


def request_native_sleep(
    action: str, *, confirmed: bool = False, typed: str = "",
) -> NativeSleepResult:
    """GUI confirmation path; no shell, no force and no sudo."""
    _, phrase = _validate_mode(action)
    if not confirmed or typed != phrase:
        return NativeSleepResult(False, "explicit_confirmation_required")
    if not assess_native_sleep(action).request_possible:
        return NativeSleepResult(False, "preflight_blocked")
    return _submit(action)


def request_terminal_sleep(action: str, *, confirmed: bool = False) -> NativeSleepResult:
    """Require both CLI --confirm and an exact typed phrase in a local TTY."""
    _, phrase = _validate_mode(action)
    if not confirmed:
        return NativeSleepResult(False, "confirmation_flag_required")
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return NativeSleepResult(False, "interactive_terminal_required")
    if not assess_native_sleep(action).request_possible:
        return NativeSleepResult(False, "preflight_blocked")
    print(
        f"WARNING: {action} can interrupt work, and resume reliability is "
        "not guaranteed even when Linux reports support."
    )
    print("Save all work before continuing.")
    try:
        typed = input(f"Type {phrase} to request the OS action: ")
    except (KeyboardInterrupt, EOFError):
        return NativeSleepResult(False, "cancelled")
    return request_native_sleep(action, confirmed=True, typed=typed)
