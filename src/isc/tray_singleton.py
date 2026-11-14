"""Single-instance user-session lock for optional ISC GNOME tray.

A durable lock file (0600) is intentionally *not* unlinked on release:
unlinking would allow two different lock inodes and duplicate tray owners.
No existing process is terminated or inspected through this lock.
"""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import stat

from .receiver import _private_runtime_root


class TrayAlreadyRunning(RuntimeError):
    """Another ISC tray holds the current user's session lock."""


class TrayLockUnavailable(RuntimeError):
    """No safe place to acquire the session lock."""


@contextmanager
def single_tray_instance(*, runtime_dir: str | Path | None = None):
    try:
        root = _private_runtime_root(runtime_dir)
        path = root / "isc-tray.lock"
        fd = os.open(
            path,
            os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
    except OSError:
        raise TrayLockUnavailable("private_runtime_unavailable") from None
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
        ):
            raise TrayLockUnavailable("unsafe_tray_lock")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise TrayAlreadyRunning("tray_already_running") from None
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
