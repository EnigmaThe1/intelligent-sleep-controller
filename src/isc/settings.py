"""Persistent user-owned ISC operator preference; NO power management actions.

Only OFF and MONITOR are currently available. ON deliberately cannot be
selected until safe automatic-suspend safeguards and hardware testing exist.
The stored preference does not install or run a daemon.
"""

from __future__ import annotations

from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
import stat
import tempfile

PROJECT_DIR = "intelligent-sleep-controller"
CONFIG_FILE = "settings.json"
MAX_CONFIG_BYTES = 1024


class ModeConfigError(ValueError):
    """Safe error code; no configuration contents or private paths exposed."""


@dataclass(frozen=True)
class ModeStatus:
    mode: str
    configured: bool


def _config_root(root: str | Path | None) -> Path:
    if root is None:
        configured = os.environ.get("XDG_CONFIG_HOME")
        root = configured if configured else Path.home() / ".config"
    root = Path(root)
    if not root.is_absolute():
        raise ModeConfigError("config_root_must_be_absolute")
    return root


def _check_directory(path: Path, *, private: bool) -> None:
    try:
        st = path.lstat()
    except OSError:
        raise ModeConfigError("config_directory_missing_or_unavailable") from None
    if (
        not stat.S_ISDIR(st.st_mode)
        or st.st_uid != os.getuid()
        or (st.st_mode & (0o077 if private else 0o022))
    ):
        raise ModeConfigError("unsafe_config_directory")


def _read_file(path: Path) -> str:
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            st = os.fstat(fd)
            if (
                not stat.S_ISREG(st.st_mode)
                or st.st_uid != os.getuid()
                or st.st_mode & 0o077
                or st.st_size > MAX_CONFIG_BYTES
            ):
                raise ModeConfigError("unsafe_mode_file")
            with os.fdopen(os.dup(fd), "rb") as stream:
                raw = stream.read(MAX_CONFIG_BYTES + 1)
            if len(raw) > MAX_CONFIG_BYTES:
                raise ModeConfigError("mode_file_too_large")
            current = path.lstat()
            if (
                (current.st_dev, current.st_ino, current.st_size,
                 current.st_mtime_ns, current.st_ctime_ns)
                !=
                (st.st_dev, st.st_ino, st.st_size,
                 st.st_mtime_ns, st.st_ctime_ns)
            ):
                raise ModeConfigError("mode_file_changed_during_read")
        finally:
            os.close(fd)
        parsed = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, ModeConfigError):
            raise
        raise ModeConfigError("mode_file_unavailable_or_invalid") from None
    if (
        not isinstance(parsed, dict)
        or set(parsed) != {"version", "mode"}
        or type(parsed["version"]) is not int
        or parsed["version"] != 1
        or parsed["mode"] not in ("off", "monitor")
    ):
        raise ModeConfigError("mode_data_invalid")
    return parsed["mode"]


def read_mode(*, config_root: str | Path | None = None) -> ModeStatus:
    """No filesystem changes. Missing settings default to OFF."""
    root = _config_root(config_root)
    try:
        root.lstat()
    except FileNotFoundError:
        return ModeStatus("off", False)
    except OSError:
        raise ModeConfigError("config_root_unavailable") from None
    _check_directory(root, private=False)
    directory = root / PROJECT_DIR
    try:
        directory.lstat()
    except FileNotFoundError:
        return ModeStatus("off", False)
    except OSError:
        raise ModeConfigError("config_directory_unavailable") from None
    _check_directory(directory, private=True)
    file = directory / CONFIG_FILE
    try:
        file.lstat()
    except FileNotFoundError:
        return ModeStatus("off", False)
    except OSError:
        raise ModeConfigError("mode_file_unavailable") from None
    return ModeStatus(_read_file(file), True)


def set_mode(mode: str, *, config_root: str | Path | None = None) -> ModeStatus:
    """Explicit opt-in persistent preference; never starts work or suspends."""
    if mode == "on":
        raise ModeConfigError("on_not_implemented")
    if mode not in ("off", "monitor"):
        raise ModeConfigError("unsupported_mode")
    root = _config_root(config_root)
    # Inspect before changing anything. Never replace an unknown setting.
    present = read_mode(config_root=root)
    if present.configured and present.mode == mode:
        return present
    try:
        root.mkdir(mode=0o700, exist_ok=True)
        _check_directory(root, private=False)
        directory = root / PROJECT_DIR
        directory.mkdir(mode=0o700, exist_ok=True)
        _check_directory(directory, private=True)
        lock_path = directory / ".settings.lock"
        fd = os.open(
            lock_path,
            os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            st = os.fstat(fd)
            if (
                not stat.S_ISREG(st.st_mode)
                or st.st_uid != os.getuid()
                or st.st_mode & 0o077
            ):
                raise ModeConfigError("unsafe_settings_lock")
            fcntl.flock(fd, fcntl.LOCK_EX)
            # Another ISC instance could have changed the mode while waiting.
            current = read_mode(config_root=root)
            if current.mode == mode and current.configured:
                return current
            final = directory / CONFIG_FILE
            payload = (json.dumps({"version": 1, "mode": mode}) + "\n").encode()
            if len(payload) > MAX_CONFIG_BYTES:
                raise ModeConfigError("mode_payload_too_large")
            stage_fd, stage_name = tempfile.mkstemp(
                prefix=".mode-stage-", dir=directory,
            )
            try:
                os.fchmod(stage_fd, 0o600)
                with os.fdopen(os.dup(stage_fd), "wb") as stream:
                    stream.write(payload)
                    stream.flush()
                os.fsync(stage_fd)
            finally:
                os.close(stage_fd)
            try:
                # File permissions/identity may have changed since read.
                if read_mode(config_root=root) != current:
                    raise ModeConfigError("settings_changed_during_write")
                os.replace(stage_name, final)
                stage_name = ""
                dir_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            finally:
                if stage_name:
                    Path(stage_name).unlink(missing_ok=True)
            return ModeStatus(mode, True)
        finally:
            os.close(fd)
    except (OSError, UnicodeError) as exc:
        raise ModeConfigError("mode_write_unavailable") from None
