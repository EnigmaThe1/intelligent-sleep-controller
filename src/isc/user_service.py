"""Opt-in systemd --user supervisor for the monitor-only ISC engine.

The tray is optional. This module touches only ISC's owned user unit.
No system-level unit, sudo, suspend, power-profile or GNOME change.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile

UNIT_NAME = "isc-monitor.service"
_SAFE_PATH = re.compile(r"/[A-Za-z0-9_/.-]+\Z", re.ASCII)


class UserServiceError(RuntimeError):
    """Stable error codes; never print host configuration contents."""


@dataclass(frozen=True)
class UserServiceStatus:
    installed: bool
    enabled: bool | None
    active: bool | None


def unit_contents(
    *, interpreter: str | None = None, source_root: str | Path | None = None,
) -> bytes:
    python = str(interpreter or sys.executable)
    source = str(source_root or Path(__file__).resolve().parents[1])
    if not _SAFE_PATH.fullmatch(python) or not _SAFE_PATH.fullmatch(source):
        raise UserServiceError("unsupported_install_path")
    return (
        "[Unit]\n"
        "Description=Intelligent Sleep Controller (monitor-only user service)\n"
        "StartLimitIntervalSec=300\n"
        "StartLimitBurst=5\n"
        "\n[Service]\n"
        "Type=simple\n"
        f"Environment=PYTHONPATH={source}\n"
        "Environment=PYTHONDONTWRITEBYTECODE=1\n"
        f"ExecStart={python} -B -m isc monitor-service\n"
        "Restart=on-failure\n"
        "RestartSec=10\n"
        "UMask=0077\n"
        "NoNewPrivileges=yes\n"
        "\n[Install]\n"
        "WantedBy=default.target\n"
    ).encode("utf-8")


def _root(config_root: str | Path | None) -> Path:
    name = config_root or os.environ.get("XDG_CONFIG_HOME") or (
        Path.home() / ".config"
    )
    root = Path(name)
    if not root.is_absolute():
        raise UserServiceError("config_root_not_absolute")
    return root


def _safe_dir(path: Path, *, private: bool = True) -> None:
    st = path.lstat()
    if (
        not stat.S_ISDIR(st.st_mode)
        or st.st_uid != os.getuid()
        or st.st_mode & (0o077 if private else 0o022)
    ):
        raise UserServiceError("unsafe_unit_directory")


def _read_owned(path: Path) -> bytes:
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            st = os.fstat(fd)
            if (
                not stat.S_ISREG(st.st_mode)
                or st.st_uid != os.getuid()
                or st.st_mode & 0o077
                or st.st_size > 4096
            ):
                raise UserServiceError("unsafe_unit_file")
            with os.fdopen(os.dup(fd), "rb") as stream:
                payload = stream.read(4097)
            if len(payload) > 4096:
                raise UserServiceError("oversized_unit")
            if (st.st_dev, st.st_ino, st.st_mtime_ns) != (
                (cur := path.lstat()).st_dev, cur.st_ino, cur.st_mtime_ns
            ):
                raise UserServiceError("unit_changed_during_read")
            return payload
        finally:
            os.close(fd)
    except OSError:
        raise UserServiceError("unit_unavailable") from None


def _run_systemd(*args: str) -> bool:
    try:
        done = subprocess.run(
            ["systemctl", "--user", *args],
            check=False, capture_output=True, text=True, timeout=12,
        )
        return done.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _unit_directory(root: Path) -> Path:
    return root / "systemd" / "user"


def inspect_user_service(
    *, config_root: str | Path | None = None,
    live: bool = True,
) -> UserServiceStatus:
    root = _root(config_root)
    unit = _unit_directory(root) / UNIT_NAME
    if unit.is_symlink():
        raise UserServiceError("unsafe_unit_file")
    if unit.exists():
        _safe_dir(root, private=False)
        _safe_dir(root / "systemd")
        _safe_dir(unit.parent)
        if _read_owned(unit) != unit_contents():
            raise UserServiceError("foreign_or_modified_unit")
        installed = True
    else:
        installed = False
    if not live or not installed:
        return UserServiceStatus(installed, None, None)
    # Only report confirmed true/false when systemctl responds successfully.
    enabled = _run_systemd("is-enabled", "--quiet", UNIT_NAME)
    active = _run_systemd("is-active", "--quiet", UNIT_NAME)
    return UserServiceStatus(installed, enabled, active)


def _sync(parent: Path) -> None:
    fd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def modify_user_service(
    action: str, *, config_root: str | Path | None = None,
) -> UserServiceStatus:
    """Explicit operator action. Never overwrites an unknown unit."""
    if action not in ("enable", "disable"):
        raise UserServiceError("invalid_service_action")
    root = _root(config_root)
    status = inspect_user_service(config_root=root, live=False)
    parent = _unit_directory(root)
    target = parent / UNIT_NAME
    if action == "enable":
        if not status.installed:
            _safe_dir(root, private=False)
            systemd = root / "systemd"
            systemd.mkdir(mode=0o700, exist_ok=True)
            _safe_dir(systemd)
            parent.mkdir(mode=0o700, exist_ok=True)
            _safe_dir(parent)
            fd, name = tempfile.mkstemp(prefix=".isc-unit-", dir=parent)
            staged = Path(name)
            try:
                with os.fdopen(fd, "wb") as writer:
                    writer.write(unit_contents())
                    writer.flush()
                    os.fsync(writer.fileno())
                # Link is exclusive, so we never overwrite a competing unit.
                os.link(staged, target)
                _sync(parent)
            except OSError:
                raise UserServiceError("unit_install_refused") from None
            finally:
                staged.unlink(missing_ok=True)
        if not _run_systemd("daemon-reload"):
            raise UserServiceError("user_daemon_reload_failed")
        if not _run_systemd("enable", "--now", UNIT_NAME):
            raise UserServiceError("user_service_enable_failed")
    else:
        if status.installed:
            if not _run_systemd("disable", "--now", UNIT_NAME):
                raise UserServiceError("user_service_disable_failed")
            if _read_owned(target) != unit_contents():
                raise UserServiceError("unit_changed_before_removal")
            target.unlink()
            _sync(parent)
            if not _run_systemd("daemon-reload"):
                raise UserServiceError("user_daemon_reload_failed")
    return inspect_user_service(config_root=root, live=True)
