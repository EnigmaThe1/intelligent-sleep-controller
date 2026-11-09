"""Explicit, reversible per-user GNOME automatic-sleep coordination.

ISC never changes GNOME policy on mode selection, startup or a preview.
A deliberate operator action can disable GNOME's *idle* suspend timeouts
on both AC and battery. Originals are recorded privately *before* writes
and restored only while the exact ISC-written values remain unchanged.

This avoids competing unattended-sleep engines. It is not permission to
enable ISC ON: workload coverage and hardware resume remain unverified.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import subprocess
from typing import Iterator

from .settings import PROJECT_DIR, _check_directory, _config_root

_SCHEMA = "org.gnome.settings-daemon.plugins.power"
_KEYS = ("sleep-inactive-ac-timeout", "sleep-inactive-battery-timeout")
_BACKUP = ".gnome-idle-timeouts.json"
_LOCK = ".gnome-idle-timeouts.lock"
_FORMAT = re.compile(r"(?:uint32 )?([0-9]{1,10})\Z", re.ASCII)
_VERSION = 1
_MAX_BACKUP = 256


class GnomePolicyError(ValueError):
    """Stable reason code, with no paths or command output."""


@dataclass(frozen=True)
class GnomePolicyStatus:
    ac_timeout: int | None
    battery_timeout: int | None
    managed: bool
    conflict: bool
    reason: str


def _read_timeout(key: str) -> int | None:
    if key not in _KEYS:
        return None
    try:
        result = subprocess.run(
            ("gsettings", "get", _SCHEMA, key),
            capture_output=True, text=True, check=False, timeout=3,
        )
        if result.returncode:
            return None
        match = _FORMAT.fullmatch(result.stdout.strip())
        if match is None:
            return None
        value = int(match.group(1))
        return value if value < 2**32 else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def _write_timeout(key: str, value: int) -> bool:
    if (key not in _KEYS or type(value) is not int
            or value < 0 or value >= 2**32):
        return False
    try:
        result = subprocess.run(
            ("gsettings", "set", _SCHEMA, key, str(value)),
            capture_output=True, text=True, check=False, timeout=3,
        )
        return result.returncode == 0
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return False


def _backup_path(config_root: str | Path | None) -> Path:
    root = _config_root(config_root)
    _check_directory(root, private=False)
    directory = root / PROJECT_DIR
    _check_directory(directory, private=True)
    return directory / _BACKUP


def _read_backup(path: Path) -> tuple[int, int] | None:
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return None
    except OSError:
        raise GnomePolicyError("gnome_backup_unavailable") from None
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077 or info.st_nlink != 1
                or info.st_size > _MAX_BACKUP):
            raise GnomePolicyError("unsafe_gnome_backup")
        raw = os.read(fd, _MAX_BACKUP + 1)
    except OSError:
        raise GnomePolicyError("gnome_backup_unavailable") from None
    finally:
        os.close(fd)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError):
        raise GnomePolicyError("gnome_backup_invalid") from None
    if (not isinstance(value, dict)
            or set(value) != {"version", "ac", "battery"}
            or type(value["version"]) is not int or value["version"] != _VERSION
            or any(type(value[k]) is not int or value[k] < 0
                   or value[k] >= 2**32 for k in ("ac", "battery"))):
        raise GnomePolicyError("gnome_backup_invalid")
    return value["ac"], value["battery"]


@contextmanager
def _exclusive(config_root: str | Path | None) -> Iterator[Path]:
    path = _backup_path(config_root)
    lock = path.with_name(_LOCK)
    try:
        fd = os.open(
            lock, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
    except OSError:
        raise GnomePolicyError("gnome_lock_unavailable") from None
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077 or info.st_nlink != 1):
            raise GnomePolicyError("unsafe_gnome_lock")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise GnomePolicyError("gnome_policy_busy") from None
        yield path
    finally:
        os.close(fd)


def _save_backup(path: Path, original: tuple[int, int]) -> None:
    payload = json.dumps({
        "version": _VERSION, "ac": original[0], "battery": original[1],
    }, separators=(",", ":")).encode("ascii")
    if len(payload) > _MAX_BACKUP:
        raise GnomePolicyError("gnome_backup_invalid")
    try:
        fd = os.open(
            path, os.O_WRONLY | os.O_CREAT | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0), 0o600,
        )
    except OSError:
        raise GnomePolicyError("gnome_backup_exists_or_unavailable") from None
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError:
        # Keep the recovery record if its creation was interrupted.
        raise GnomePolicyError("gnome_backup_write_failed") from None


def inspect_gnome_policy(
    *, config_root: str | Path | None = None,
) -> GnomePolicyStatus:
    """Read-only status. Never infer unverified power settings are disabled."""
    ac, battery = (_read_timeout(k) for k in _KEYS)
    try:
        backup = _read_backup(_backup_path(config_root))
    except (GnomePolicyError, OSError, ValueError):
        return GnomePolicyStatus(ac, battery, False, True, "backup_unverified")
    if ac is None or battery is None:
        return GnomePolicyStatus(ac, battery, backup is not None, True,
                                 "gnome_policy_unverified")
    if backup is not None and (ac, battery) != (0, 0):
        return GnomePolicyStatus(ac, battery, True, True,
                                 "gnome_policy_changed_externally")
    return GnomePolicyStatus(
        ac, battery, backup is not None,
        ac != 0 or battery != 0,
        "gnome_idle_disabled" if ac == battery == 0 else "gnome_idle_enabled",
    )


def change_gnome_policy(
    operation: str, *, confirmed: bool = False,
    config_root: str | Path | None = None,
) -> GnomePolicyStatus:
    """Operator-confirmed reversible timeout change; no automatic callers.

    Refuses to clobber any external edits. If the second write fails,
    attempt to restore the first value and retain the backup for recovery.
    """
    if operation not in ("disable", "restore") or confirmed is not True:
        raise GnomePolicyError("explicit_confirmation_required")
    try:
        with _exclusive(config_root) as path:
            saved = _read_backup(path)
            current = tuple(_read_timeout(key) for key in _KEYS)
            if any(value is None for value in current):
                raise GnomePolicyError("gnome_policy_unverified")

            if operation == "disable":
                if saved is not None:
                    if current != (0, 0):
                        raise GnomePolicyError("gnome_policy_changed_externally")
                    return inspect_gnome_policy(config_root=config_root)
                if current == (0, 0):
                    return inspect_gnome_policy(config_root=config_root)
                _save_backup(path, current)

                for index, key in enumerate(_KEYS):
                    if current[index] != 0:
                        if not _write_timeout(key, 0) or _read_timeout(key) != 0:
                            # Restore only when the other key still matches
                            # the value ISC wrote; never overwrite edits.
                            for j in range(index + 1):
                                actual = _read_timeout(_KEYS[j])
                                if actual == 0 and current[j] != 0:
                                    _write_timeout(_KEYS[j], current[j])
                            raise GnomePolicyError("gnome_policy_disable_incomplete")
                return inspect_gnome_policy(config_root=config_root)

            if saved is None:
                raise GnomePolicyError("gnome_backup_not_owned")
            if current != (0, 0):
                raise GnomePolicyError("gnome_policy_changed_externally")
            for index, key in enumerate(_KEYS):
                if saved[index] != 0:
                    if not _write_timeout(key, saved[index]) or (
                        _read_timeout(key) != saved[index]
                    ):
                        raise GnomePolicyError("gnome_policy_restore_incomplete")
            # Recovery state is retained on partial failure.
            path.unlink()
            return inspect_gnome_policy(config_root=config_root)
    except GnomePolicyError:
        raise
    except (OSError, ValueError):
        raise GnomePolicyError("gnome_policy_unavailable") from None
