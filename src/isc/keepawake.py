"""Explicitly requested systemd sleep/idle inhibitor with owner-death release.

A local foreground CLI or tray owns the child and can cancel it. The
inhibitor is NEVER started by a timer, hook, ISC mode change, or autostart.
It does not modify GNOME power settings or ISC automatic-mode preferences.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import os
import secrets
import shlex
import signal
import subprocess
import time
import sys
from pathlib import Path

from .inhibitors import _COMMAND as LOGIND_INHIBITORS_COMMAND

PRESETS_MINUTES = (15, 30, 60, 120, 240)
INHIBITOR_EXECUTABLE = "/usr/bin/systemd-inhibit"
LOCK_OWNER = "Intelligent Sleep Controller"


class KeepAwakeError(RuntimeError):
    """Error code with no environment secrets or process details."""


@dataclass(frozen=True)
class KeepAwakeStatus:
    requested: bool
    remaining_seconds: int | None = 0


def inhibitor_command(minutes: int | None, marker: str) -> tuple[str, ...]:
    """No shell and no user-supplied command text."""
    if minutes is not None and (type(minutes) is not int or minutes not in PRESETS_MINUTES):
        raise KeepAwakeError("unsupported_duration")
    if not marker or not marker.isascii() or not marker.isalnum():
        raise KeepAwakeError("invalid_marker")
    return (
        INHIBITOR_EXECUTABLE, "--what=idle:sleep", "--mode=block",
        f"--who={LOCK_OWNER}", f"--why=ISC timed Keep Awake {marker}",
        sys.executable, "-B", "-m", "isc.keepawake_worker",
        "forever" if minutes is None else str(minutes * 60),
    )


def _observed_lock(marker: str) -> bool:
    """Verify our unique inhibitor, without exposing other users' records."""
    try:
        done = subprocess.run(
            list(LOGIND_INHIBITORS_COMMAND),
            capture_output=True, text=True, check=False, timeout=1,
        )
        if done.returncode:
            return False
        tokens = shlex.split(done.stdout, posix=True)
        if len(tokens) < 2 or tokens[:1] != ["a(ssssuu)"]:
            return False
        if not tokens[1].isascii() or not tokens[1].isdecimal():
            return False
        count = int(tokens[1])
        if count > 4096 or len(tokens) != 2 + 6 * count:
            return False
        for i in range(count):
            what, who, why, mode, uid, _pid = tokens[2+6*i:8+6*i]
            if (
                who == LOCK_OWNER
                and why == f"ISC timed Keep Awake {marker}"
                and mode == "block"
                and {"sleep", "idle"}.issubset(what.split(":"))
                and uid == str(os.getuid())
            ):
                return True
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return False


def _minimal_env() -> dict[str, str]:
    """Never leak unrelated tokens or credentials to an inhibitor child."""
    env = {
        name: os.environ[name]
        for name in (
            "HOME", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS",
            "LANG", "LC_ALL", "TZ",
        )
        if name in os.environ
    }
    # Local worker must import the exact ISC version that owns this lock.
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


class KeepAwakeManager:
    """One bounded local inhibitor, owned by the object that launched it."""

    def __init__(self):
        self._child: subprocess.Popen | None = None
        self._expires_at: float | None = 0.0
        self._marker: str | None = None

    def status(self) -> KeepAwakeStatus:
        child = self._child
        if child is None or child.poll() is not None:
            return KeepAwakeStatus(False)
        if self._expires_at is None:
            return KeepAwakeStatus(True, None)
        remaining = max(0, math.ceil(self._expires_at - time.monotonic()))
        return KeepAwakeStatus(remaining > 0, remaining)

    def start(self, minutes: int | None) -> KeepAwakeStatus:
        # Validate before stopping an existing lease.
        marker = secrets.token_hex(8)
        argv = inhibitor_command(minutes, marker)
        if not all(os.access(program, os.X_OK) for program in (
            INHIBITOR_EXECUTABLE, sys.executable,
        )):
            raise KeepAwakeError("systemd_inhibit_or_sleep_unavailable")
        self.cancel()
        try:
            child = subprocess.Popen(
                list(argv), env=_minimal_env(),
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                start_new_session=True,
            )
        except OSError:
            raise KeepAwakeError("inhibitor_start_failed") from None
        self._child = child
        self._marker = marker
        self._expires_at = None if minutes is None else time.monotonic() + minutes * 60
        # The lock is only considered active after it is actually observed in
        # logind. A live subprocess alone is insufficient evidence.
        deadline = time.monotonic() + 2.5
        try:
            while time.monotonic() < deadline:
                if child.poll() is not None:
                    raise KeepAwakeError("inhibitor_exited")
                if _observed_lock(marker):
                    return self.status()
                time.sleep(0.1)
            raise KeepAwakeError("inhibitor_not_verified")
        except BaseException:
            self.cancel()
            raise

    def cancel(self) -> None:
        """Close the ownership pipe first; let logind release the native lock."""
        child = self._child
        if child is None:
            return
        if child.poll() is not None:
            self._child = None
            self._marker = None
            self._expires_at = 0.0
            return
        completed = False
        try:
            if child.stdin is None:
                raise KeepAwakeError("inhibitor_owner_pipe_missing")
            # No signal or guessed PID required in normal operation. The
            # worker terminates on EOF and systemd-inhibit releases its lock.
            if not child.stdin.closed:
                child.stdin.close()
            try:
                child.wait(timeout=3)
                completed = True
            except subprocess.TimeoutExpired:
                # Only target the exact process group explicitly created by
                # ISC. Never kill a guessed or unrelated process.
                if child.poll() is None:
                    if os.getpgid(child.pid) != child.pid:
                        raise KeepAwakeError("inhibitor_process_identity_changed")
                    os.killpg(child.pid, signal.SIGTERM)
                try:
                    child.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    # The child has not acknowledged SIGTERM. Verify the
                    # specific still-owned process group before escalation.
                    if child.poll() is None:
                        if os.getpgid(child.pid) != child.pid:
                            raise KeepAwakeError("inhibitor_process_identity_changed")
                        os.killpg(child.pid, signal.SIGKILL)
                    child.wait(timeout=1)
                completed = True
        except (OSError, PermissionError, subprocess.TimeoutExpired):
            raise KeepAwakeError("inhibitor_cancellation_unverified") from None
        finally:
            if completed or child.poll() is not None:
                self._child = None
                self._marker = None
                self._expires_at = 0.0

    def close(self) -> None:
        self.cancel()
