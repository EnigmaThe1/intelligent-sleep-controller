"""Ephemeral, same-user Unix-socket event receiver: MONITOR-ONLY.

No services, hooks or power settings are installed. Transport conveys only
sanitised lifecycle metadata and cannot ever prove safe sleep eligibility.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import json
import errno
import fcntl
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

MONITOR_DIRECTORY_NAME = "isc-monitor"


def _private_runtime_root(runtime_dir: str | Path | None) -> Path:
    if runtime_dir is None:
        runtime_dir = os.environ.get("XDG_RUNTIME_DIR", "")
    if not runtime_dir:
        raise OSError("No private XDG_RUNTIME_DIR is available")
    root = Path(runtime_dir)
    root_stat = root.lstat()
    if (
        not stat.S_ISDIR(root_stat.st_mode)
        or root_stat.st_uid != os.getuid()
        or root_stat.st_mode & 0o077
    ):
        raise PermissionError("Runtime directory must be owned and private")
    return root


def discover_monitor_socket(*, runtime_dir: str | Path | None = None) -> Path | None:
    """Discover ONLY an explicitly advertised, private same-user Unix socket.

    Never create, delete, or modify any path while discovering a monitor.
    A discovered socket does not authenticate an application's event.
    """
    try:
        root = _private_runtime_root(runtime_dir)
        directory = root / MONITOR_DIRECTORY_NAME
        directory_stat = directory.lstat()
        if (
            not stat.S_ISDIR(directory_stat.st_mode)
            or directory_stat.st_uid != os.getuid()
            or directory_stat.st_mode & 0o077
        ):
            return None
        address = directory / "events.sock"
        sock_stat = address.lstat()
        if (
            not stat.S_ISSOCK(sock_stat.st_mode)
            or sock_stat.st_uid != os.getuid()
            or sock_stat.st_mode & 0o077
        ):
            return None
        return address
    except (OSError, ValueError):
        return None


def send_discovered_observation(
    observation: HookObservation, *, runtime_dir: str | Path | None = None
) -> bool:
    """No monitor? Do nothing. Never wait for one or block an agent."""
    address = discover_monitor_socket(runtime_dir=runtime_dir)
    return False if address is None else send_observation(address, observation)



def _lock_monitor_root(root: Path) -> int:
    """Serialize advertised monitor ownership, including recovery of crashes."""
    path = root / "isc-monitor.lock"
    fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or info.st_mode & 0o077
        ):
            raise PermissionError("unsafe_monitor_lock")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except BaseException:
        os.close(fd)
        raise


def _prepare_monitor_directory(root: Path) -> Path:
    """Recover only a private, demonstrably stale ISC Unix socket.

    An active, foreign, insecure or ambiguous path is never removed.
    Caller MUST hold the per-runtime monitor lock.
    """
    directory = root / MONITOR_DIRECTORY_NAME
    try:
        directory.mkdir(mode=0o700)
    except FileExistsError:
        pass
    info = directory.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
    ):
        raise PermissionError("unsafe_monitor_directory")
    contents = tuple(directory.iterdir())
    if not contents:
        return directory
    socket_path = directory / "events.sock"
    if contents != (socket_path,):
        raise OSError("unexpected_monitor_directory_contents")
    sock_info = socket_path.lstat()
    if (
        not stat.S_ISSOCK(sock_info.st_mode)
        or sock_info.st_uid != os.getuid()
        or sock_info.st_mode & 0o077
    ):
        raise PermissionError("unsafe_existing_socket")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
        peer.settimeout(0.2)
        try:
            peer.connect(str(socket_path))
        except OSError as exc:
            if exc.errno != errno.ECONNREFUSED:
                raise OSError("existing_socket_state_unverified") from None
        else:
            raise OSError("existing_monitor_socket_active")
    # No listener exists, and only the exact unchanged inode is removed.
    current = socket_path.lstat()
    if (current.st_dev, current.st_ino, current.st_mode) != (
        sock_info.st_dev, sock_info.st_ino, sock_info.st_mode
    ):
        raise OSError("existing_socket_changed")
    socket_path.unlink()
    return directory


MAX_MESSAGE_BYTES = 1024
MAX_HEALTH_BYTES = 512
HEALTH_REQUEST = b"ISC-MONITOR-STATUS-V1"


@dataclass(frozen=True)
class MonitorHealth:
    """Aggregate, read-only receiver health; NOT permission to suspend."""

    accepted: int
    rejected: int
    observed_active: int
    observed_waiting_human: int
    observed_unknown: int
    last_event_age_seconds: int | None
    coverage_verified: bool = False


def read_monitor_health(*, runtime_dir: str | Path | None = None) -> MonitorHealth | None:
    """Query the live same-user receiver without writing user data or logs."""
    address = discover_monitor_socket(runtime_dir=runtime_dir)
    if address is None:
        return None
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
            peer.settimeout(0.5)
            peer.connect(str(address))
            peer.sendall(HEALTH_REQUEST)
            peer.shutdown(socket.SHUT_WR)
            raw = bytearray()
            while len(raw) <= MAX_HEALTH_BYTES:
                part = peer.recv(MAX_HEALTH_BYTES + 1 - len(raw))
                if not part:
                    break
                raw.extend(part)
            if len(raw) > MAX_HEALTH_BYTES:
                return None
        obj = json.loads(bytes(raw))
        allowed = {
            "accepted", "rejected", "observed_active",
            "observed_waiting_human", "observed_unknown",
            "last_event_age_seconds", "coverage_verified",
        }
        if not isinstance(obj, dict) or set(obj) != allowed:
            return None
        counts = ("accepted", "rejected", "observed_active",
                  "observed_waiting_human", "observed_unknown")
        if any(type(obj[k]) is not int or not 0 <= obj[k] <= 2**31 for k in counts):
            return None
        age = obj["last_event_age_seconds"]
        if age is not None and (type(age) is not int or not 0 <= age <= 2**31):
            return None
        # Receiver observations are not an authoritative coverage proof.
        if obj["coverage_verified"] is not False:
            return None
        return MonitorHealth(**obj)
    except (OSError, ValueError, TypeError, UnicodeError):
        return None


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

    def __init__(
        self, *, runtime_dir: str | Path | None = None,
        advertise: bool = False,
    ) -> None:
        root = _private_runtime_root(runtime_dir)
        self._lock_fd: int | None = None
        self._closed = False
        self._listener: socket.socket | None = None
        self._socket_inode: int | None = None
        try:
            if advertise:
                self._lock_fd = _lock_monitor_root(root)
                self._dir = _prepare_monitor_directory(root)
            else:
                self._dir = Path(tempfile.mkdtemp(prefix="isc-", dir=root))
            self.path = self._dir / "events.sock"
            self.registry = WorkRegistry()
            self.counts: Counter[str] = Counter()
            self.event_counts: Counter[tuple[str, str]] = Counter()
            self.accepted = 0
            self.rejected = 0
            self._last_event_at: float | None = None
            self._listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._listener.bind(str(self.path))
            self.path.chmod(0o600)
            self._listener.listen(16)
            self._socket_inode = self.path.lstat().st_ino
        except BaseException:
            if self._listener is not None:
                self._listener.close()
            # Do not delete a previously existing socket on an early failure.
            if self._socket_inode is not None:
                self.path.unlink(missing_ok=True)
            if hasattr(self, "_dir"):
                try:
                    self._dir.rmdir()
                except OSError:
                    pass
            self._release_lock()
            raise

    def _release_lock(self) -> None:
        if self._lock_fd is not None:
            os.close(self._lock_fd)
            self._lock_fd = None

    def __enter__(self) -> LocalMonitor:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _handle_one(self) -> None:
        connection, _ = self._listener.accept()
        with connection:
            connection.settimeout(0.2)
            try:
                credential_option = getattr(socket, "SO_PEERCRED", None)
                if not isinstance(credential_option, int):
                    self.rejected += 1
                    return
                raw = connection.getsockopt(
                    socket.SOL_SOCKET, credential_option,
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
                if bytes(payload) == HEALTH_REQUEST:
                    self._reply_health(connection)
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
                self.event_counts[(source, event_name)] += 1
                self.accepted += 1
                self._last_event_at = time.monotonic()
            except (OSError, ValueError, socket.timeout):
                self.rejected += 1

    def _reply_health(self, connection: socket.socket) -> None:
        """No session fingerprints, PIDs, commands or prompts leave receiver."""
        now = time.monotonic()
        items = self.registry.observed_work(now=now)
        states = Counter(item.state.value for item in items)
        response = {
            "accepted": self.accepted,
            "rejected": self.rejected,
            "observed_active": states["active"] + states["waiting_external"],
            "observed_waiting_human": states["waiting_human"],
            "observed_unknown": states["unknown"],
            "last_event_age_seconds": (
                int(max(0, now - self._last_event_at))
                if self._last_event_at is not None else None
            ),
            "coverage_verified": False,
        }
        packet = json.dumps(response, separators=(",", ":")).encode("ascii")
        if len(packet) <= MAX_HEALTH_BYTES:
            connection.sendall(packet)

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
        try:
            self._dir.rmdir()
        finally:
            self._release_lock()
