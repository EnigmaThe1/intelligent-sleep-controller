"""Optional local MONITOR process owned by the foreground ISC tray.

This module runs ONLY a monitor-only ISC child. It never installs, enables,
or starts a systemd unit, touches GNOME power settings, or requests sleep.
It cannot terminate a monitor started by another application.
"""

from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import sys

from .receiver import discover_monitor_socket
from .mode_gate import resolve_mode
from .settings import ModeStatus


class TrayMonitorProcess:
    """Manage exactly one child launched by this tray instance."""

    def __init__(self) -> None:
        self._child: subprocess.Popen | None = None
        self._failed = False

    def _spawn(self) -> None:
        if self._child is not None:
            return
        # A different tray or explicit CLI monitor may own this socket.
        # Never try to replace it or claim ownership.
        if discover_monitor_socket() is not None:
            return
        source_root = str(Path(__file__).resolve().parents[1])
        # Do not inherit unrelated API keys, agent credentials, or secrets.
        env = {
            name: os.environ[name]
            for name in (
                "HOME", "XDG_CONFIG_HOME", "XDG_RUNTIME_DIR",
                "LANG", "LC_ALL", "TZ",
            )
            if name in os.environ
        }
        env["PYTHONPATH"] = source_root
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        try:
            self._child = subprocess.Popen(
                [sys.executable, "-B", "-m", "isc", "serve", "--seconds", "0"],
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                start_new_session=True,
            )
        except OSError:
            self._failed = True

    def reconcile(self, *, mode: str, config_valid: bool) -> str:
        """Synchronize only the tray's own child with saved operator preference.

        Does not restart an unexpectedly crashed child: avoids crash loops.
        An already-existing monitor is left untouched and reported separately.
        """
        effective = resolve_mode(ModeStatus(mode, config_valid)) if config_valid else resolve_mode(None)
        if not effective.monitor_allowed:
            self.stop()
            self._failed = False
            return "invalid_configuration" if not effective.configuration_valid else "off"

        if self._child is not None:
            if self._child.poll() is not None:
                self._child = None
                self._failed = True
            else:
                return (
                    "running"
                    if discover_monitor_socket() is not None
                    else "starting"
                )
        if self._failed:
            return "failed"
        if discover_monitor_socket() is not None:
            return "external"
        self._spawn()
        return "failed" if self._failed else "starting"

    def stop(self) -> None:
        """Stop only our own child; prefer SIGINT for clean socket teardown."""
        child, self._child = self._child, None
        if child is None or child.poll() is not None:
            return
        try:
            child.send_signal(signal.SIGINT)
            child.wait(timeout=3)
            return
        except (ProcessLookupError, PermissionError):
            return
        except subprocess.TimeoutExpired:
            pass
        # Fallback is limited to this same child process, never a process
        # group or guessed PID. It may leave a stale private runtime path.
        try:
            child.terminate()
            child.wait(timeout=2)
            return
        except (ProcessLookupError, PermissionError):
            return
        except subprocess.TimeoutExpired:
            pass
        child.kill()
        child.wait(timeout=2)

    def close(self) -> None:
        """Releasing the tray cannot leave its monitor child running."""
        self.stop()
