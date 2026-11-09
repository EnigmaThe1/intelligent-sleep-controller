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
from typing import Callable

_MATCH = ("type='signal',sender='org.freedesktop.systemd1',"
          "path='/org/freedesktop/systemd1',"
          "interface='org.freedesktop.systemd1.Manager'")
_COMMAND = ("busctl", "--user", "--no-pager", "--match=" + _MATCH,
            "monitor", "org.freedesktop.systemd1")
_ALLOWED_ENV = ("PATH", "HOME", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS",
                "LANG", "LC_ALL")
_HEADER = re.compile(
    rb"\A\xe2\x80\xa3 Type=signal[^\n]{0,200}\n"
    rb"[ \t]+Sender=(?P<sender>:[0-9]+\.[0-9]+)[ \t]+"
    rb"Path=/org/freedesktop/systemd1[ \t]+"
    rb"Interface=org\.freedesktop\.systemd1\.Manager[ \t]+"
    rb"Member=(JobNew|JobRemoved|UnitNew|UnitRemoved)[ \t]*(?:\r?\n)"
)
_BANNER = b"Monitoring bus message stream.\n"
_MAX_BYTES = 16384


@dataclass(frozen=True)
class LiveSignalProbe:
    state: str
    candidate_events: int | None = None
    coverage_verified: bool = False
    event_delivery_verified: bool = False
    task_owner_authenticated: bool = False


def _first_frame_match(raw: bytes) -> re.Match[bytes] | None:
    """Accept only an initial busctl header, never a later payload lookalike.

    The exact optional monitor banner is the only permitted prefix.
    Text-format busctl is still not a reliable D-Bus framing protocol,
    so even a matching first header is diagnostic and unauthenticated.
    """
    if not isinstance(raw, bytes):
        return None
    stream = raw[len(_BANNER):] if raw.startswith(_BANNER) else raw
    return _HEADER.match(stream)


def _recognise_header(raw: bytes) -> bool:
    """Conservative first-header recognition; never scans payload text."""
    return _first_frame_match(raw) is not None


def probe_live_user_signal(
    *, seconds: int = 3,
    _sender_callback: Callable[[str], None] | None = None,
) -> LiveSignalProbe:
    """Observe one candidate header; bounded bytes/time, kill and reap.

    Only an explicit invocation runs the command. No synthetic events, no
    raw headers returned. Quiet/timeout and early exit remain UNKNOWN.
    """
    unknown = LiveSignalProbe("UNKNOWN")
    if (type(seconds) is not int or not 1 <= seconds <= 10
            or (_sender_callback is not None and not callable(_sender_callback))):
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
                match = _first_frame_match(bytes(raw))
                if match is not None:
                    if _sender_callback is not None:
                        # Ephemeral private unique name, never in result/log.
                        # Callback failure must not confirm an event.
                        _sender_callback(match.group("sender").decode("ascii"))
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
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
            if proc.stdout is not None:
                proc.stdout.close()
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=1)
