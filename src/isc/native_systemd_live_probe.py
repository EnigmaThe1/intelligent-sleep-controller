"""Optional bounded live user-bus signal-header probe for systemd 255.

NOT connected to MONITOR/service/CLI/sleep policy. The stream is filtered
to systemd Manager signals; bytes are ephemeral and never logged. Recognising
a header is observational evidence only, NOT trusted owner, reliable delivery,
job completion, or suspend eligibility. No services are started or changed.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
import re
import selectors
import subprocess
import time

_MATCH = ("type='signal',sender='org.freedesktop.systemd1',"
          "path='/org/freedesktop/systemd1',"
          "interface='org.freedesktop.systemd1.Manager'")
_COMMAND = ("busctl", "--user", "--no-pager", "--match=" + _MATCH,
            "monitor", "org.freedesktop.systemd1")
_ALLOWED_ENV = ("PATH", "HOME", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS",
                "LANG", "LC_ALL")
_HEADER = re.compile(
    rb"(?m)^\s*Sender=:[0-9]+\.[0-9]+\s+"
    rb"Path=/org/freedesktop/systemd1\s+"
    rb"Interface=org\.freedesktop\.systemd1\.Manager\s+"
    rb"Member=(JobNew|JobRemoved|UnitNew|UnitRemoved)\s*$"
)
_MAX_BYTES = 16384


@dataclass(frozen=True)
class LiveSignalProbe:
    state: str
    candidate_events: int | None = None
    coverage_verified: bool = False
    event_delivery_verified: bool = False
    task_owner_authenticated: bool = False


def _recognise_header(raw: bytes) -> bool:
    """Require both busctl's signal header and manager member header.

    This cannot authenticate the sender; a later adapter must correlate the
    unique bus name to the systemd manager and handle owner changes.
    """
    match = _HEADER.search(raw)
    if match is None:
        return False
    prefix = raw[:match.start()]
    return b"Type=signal" in prefix and (
        b"\xe2\x80\xa3" in prefix or b"Type=signal" in prefix
    )


def probe_live_user_signal(*, seconds: int = 3) -> LiveSignalProbe:
    """Observe one candidate header; bounded bytes/time, kill and reap.

    Only an explicit invocation runs the command. No synthetic events, no
    raw headers returned. Quiet/timeout and early exit remain UNKNOWN.
    """
    unknown = LiveSignalProbe("UNKNOWN")
    if type(seconds) is not int or not 1 <= seconds <= 10:
        return unknown
    env = {key: os.environ[key] for key in _ALLOWED_ENV if key in os.environ}
    env["SYSTEMD_COLORS"] = "0"
    proc = None
    selected = None
    try:
        proc = subprocess.Popen(
            _COMMAND, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0,
        )
        if proc.stdout is None:
            return unknown
        selected = selectors.DefaultSelector()
        selected.register(proc.stdout, selectors.EVENT_READ)
        deadline = time.monotonic() + seconds
        raw = bytearray()
        while time.monotonic() < deadline:
            ready = selected.select(timeout=min(0.2, max(0, deadline - time.monotonic())))
            if ready:
                block = os.read(proc.stdout.fileno(), min(4096, _MAX_BYTES - len(raw)))
                if not block:
                    return unknown
                raw.extend(block)
                if len(raw) >= _MAX_BYTES:
                    return unknown
                if _recognise_header(bytes(raw)):
                    return LiveSignalProbe("CANDIDATE_SIGNAL_UNVERIFIED", 1)
            if proc.poll() is not None:
                return unknown
        return LiveSignalProbe("QUIET_UNVERIFIED")
    except (OSError, ValueError, RuntimeError):
        return unknown
    finally:
        if selected is not None:
            selected.close()
        if proc is not None:
            if proc.poll() is None:
                proc.kill()
            try:
                proc.communicate(timeout=1)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=1)
