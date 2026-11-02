"""Ephemeral, same-user Unix-socket event receiver: MONITOR-ONLY.

No services, hooks or power settings are installed. Transport conveys only
sanitised lifecycle metadata and cannot ever prove safe sleep eligibility.
"""

from __future__ import annotations

from collections import Counter
import json
import math
import os
from pathlib import Path
import re
import select
import socket
import stat
import struct
import tempfile
import time

from .events import hook_observation, interpret
from .hook_payload import HookObservation, SUPPORTED_SOURCES
from .registry import WorkRegistry

MAX_MESSAGE_BYTES = 1024
_EVENT = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}\Z", re.ASCII)
_FINGERPRINT = re.compile(r"[0-9a-f]{32}\Z", re.ASCII)


def encode_observation(value: HookObservation) -> bytes:
    """Only these 3 fields ever leave the hook-payload parser."""
    if (
        not isinstance(value, HookObservation)
        or value.source not in SUPPORTED_SOURCES
        or _EVENT.fullmatch(value.event_name) is None
        or _FINGERPRINT.fullmatch(value.session_fingerprint) is None
    ):
        raise ValueError("invalid_observation")
    message = json.dumps(
        {
            "source": value.source,
            "event_name": value.event_name,
            "session_fingerprint": value.session_fingerprint,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    if len(message) > MAX_MESSAGE_BYTES:
        raise ValueError("oversized_observation")
    return message


def decode_observation(payload: bytes) -> tuple[str, str, str] | None:
    """Do not trust client-supplied state, timestamps, or task completion."""
    if not isinstance(payload, bytes) or len(payload) > MAX_MESSAGE_BYTES:
        return None
    try:
        data = json.loads(payload.decode("ascii"))
    except (UnicodeError, ValueError):
        return None
    if not isinstance(data, dict) or set(data) != {
        "source", "event_name", "session_fingerprint"
    }:
        return None
    source, event, fingerprint = (
        data["source"], data["event_name"], data["session_fingerprint"]
    )
    if (
        not isinstance(source, str) or source not in SUPPORTED_SOURCES
        or not isinstance(event, str) or _EVENT.fullmatch(event) is None
        or not isinstance(fingerprint, str)
        or _FINGERPRINT.fullmatch(fingerprint) is None
    ):
        return None
    return source, event, fingerprint


def send_observation(path: str | Path, observation: HookObservation) -> bool:
    """Best-effort bounded local send; no stdout, network or credentials.

    Failures must never block an upstream coding agent. A successful send
    does not mean a receiver has confirmed or processed the event.
    """
    try:
        wire = encode_observation(observation)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
            peer.settimeout(0.2)
            peer.connect(str(path))
            peer.sendall(wire)
        return True
    except (OSError, ValueError, TypeError):
        return False


class LocalMonitor:
    """Time-bounded foreground monitor in a private runtime subdirectory."""

    def __init__(self, *, runtime_dir: str | Path | None = None) -> None:
        if runtime_dir is None:
            runtime_dir = os.environ.get("XDG_RUNTIME_DIR", "")
        if not runtime_dir:
            raise OSError("No private XDG_RUNTIME_DIR is available")
        root = Path(runtime_dir)
        root_stat = root.stat()
        if (
            not stat.S_ISDIR(root_stat.st_mode)
            or root_stat.st_uid != os.getuid()
            or root_stat.st_mode & 0o077
        ):
            raise PermissionError("Runtime directory must be owned and private")
        self._dir = Path(tempfile.mkdtemp(prefix="isc-", dir=root))
        self.path = self._dir / "events.sock"
        self.registry = WorkRegistry()
        self.counts: Counter[str] = Counter()
        self.accepted = 0
        self.rejected = 0
        self._closed = False
        self._listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self._listener.bind(str(self.path))
            self.path.chmod(0o600)
            self._listener.listen(16)
            self._socket_inode = self.path.lstat().st_ino
        except BaseException:
            self._listener.close()
            self.path.unlink(missing_ok=True)
            self._dir.rmdir()
            raise

    def __enter__(self) -> LocalMonitor:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _handle_one(self) -> None:
        connection, _ = self._listener.accept()
        with connection:
            connection.settimeout(0.2)
            try:
                if not hasattr(socket, "SO_PEERCRED"):
                    self.rejected += 1
                    return
                raw = connection.getsockopt(
                    socket.SOL_SOCKET, socket.SO_PEERCRED,
                    struct.calcsize("3i")
                )
                _pid, uid, _gid = struct.unpack("3i", raw)
                if uid != os.getuid():
                    self.rejected += 1
                    return
                payload = bytearray()
                while len(payload) <= MAX_MESSAGE_BYTES:
                    chunk = connection.recv(MAX_MESSAGE_BYTES + 1 - len(payload))
                    if not chunk:
                        break
                    payload.extend(chunk)
                if len(payload) > MAX_MESSAGE_BYTES:
                    self.rejected += 1
                    return
                fields = decode_observation(bytes(payload))
                if fields is None:
                    self.rejected += 1
                    return
                source, event_name, session_fingerprint = fields
                state = interpret(
                    hook_observation(source, event_name, session_fingerprint)
                ).state
                self.registry.report(
                    source, session_fingerprint, state,
                    now=time.monotonic(), ttl_seconds=30.0
                )
                self.counts[state.value] += 1
                self.accepted += 1
            except (OSError, ValueError, socket.timeout):
                self.rejected += 1

    def run_for(self, duration_seconds: float) -> None:
        """Observe only; never change system services or suspend the machine."""
        if (
            not isinstance(duration_seconds, (float, int))
            or not math.isfinite(duration_seconds)
            or not (0 < duration_seconds <= 300)
        ):
            raise ValueError("duration must be >0 and <=300 seconds")
        end = time.monotonic() + duration_seconds
        while True:
            remaining = end - time.monotonic()
            if remaining <= 0:
                return
            readable, _, _ = select.select(
                [self._listener], [], [], min(0.5, remaining)
            )
            if readable:
                self._handle_one()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._listener.close()
        try:
            if self.path.lstat().st_ino == self._socket_inode:
                self.path.unlink()
        except FileNotFoundError:
            pass
        self._dir.rmdir()
