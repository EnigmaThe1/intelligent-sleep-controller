"""Explicit opt-in user-session autostart for the optional ISC tray.

Only writes the single user-owned ISC .desktop file. No GNOME settings,
service manager changes, background process launches, or suspend calls.
Foreign or changed startup files are never overwritten or removed.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import stat
import sys
import tempfile

DESKTOP_NAME = "intelligent-sleep-controller.desktop"
MAX_DESKTOP_BYTES = 4096
_SAFE_EXEC_PATH = re.compile(r"/[A-Za-z0-9_/.-]+\Z", re.ASCII)


class AutostartError(ValueError):
    """No existing file contents are included in error messages."""


@dataclass(frozen=True)
class AutostartStatus:
    state: str  # disabled | enabled | unmanaged
    changed: bool = False


def _root(config_root: str | Path | None) -> Path:
    if config_root is None:
        config_root = os.environ.get("XDG_CONFIG_HOME") or (
            Path.home() / ".config"
        )
    root = Path(config_root)
    if not root.is_absolute():
        raise AutostartError("config_root_not_absolute")
    return root


def _check_directory(directory: Path) -> None:
    try:
        current = directory.lstat()
    except OSError:
        raise AutostartError("configuration_directory_unavailable") from None
    if (
        not stat.S_ISDIR(current.st_mode)
        or current.st_uid != os.getuid()
        or current.st_mode & 0o022
    ):
        raise AutostartError("unsafe_configuration_directory")


def desktop_contents(
    *, interpreter: str | None = None, source_root: str | Path | None = None,
) -> bytes:
    """A direct Exec command, not a shell invocation or remote dependency."""
    exe = str(interpreter or sys.executable)
    source = str(source_root or Path(__file__).resolve().parents[1])
    if not _SAFE_EXEC_PATH.fullmatch(exe) or not _SAFE_EXEC_PATH.fullmatch(source):
        raise AutostartError("unsupported_autostart_executable_path")
    document = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Intelligent Sleep Controller\n"
        "Comment=Optional local power-management tray\n"
        "Icon=system-suspend-symbolic\n"
        f"TryExec={exe}\n"
        "Exec=/usr/bin/env PYTHONDONTWRITEBYTECODE=1 "
        f"PYTHONPATH={source} {exe} -B -m isc tray\n"
        "Terminal=false\n"
        "NoDisplay=true\n"
        "X-GNOME-Autostart-enabled=true\n"
    )
    return document.encode("utf-8")


def _is_isc_desktop(raw: bytes) -> bool:
    """Match the entire ISC desktop entry, including historical paths.

    The filename or display Name alone must never imply ownership.
    """
    try:
        text = raw.decode("utf-8")
    except UnicodeError:
        return False
    match_try = re.search(r"(?m)^TryExec=(/[A-Za-z0-9_/.-]+)$", text)
    match_exec = re.search(
        r"(?m)^Exec=/usr/bin/env PYTHONDONTWRITEBYTECODE=1 "
        r"PYTHONPATH=(/[A-Za-z0-9_/.-]+) "
        r"(/[A-Za-z0-9_/.-]+) -B -m isc tray$", text,
    )
    if match_try is None or match_exec is None:
        return False
    if match_try.group(1) != match_exec.group(2):
        return False
    try:
        return raw == desktop_contents(
            interpreter=match_try.group(1),
            source_root=match_exec.group(1),
        )
    except AutostartError:
        return False


def _existing_bytes(path: Path) -> bytes:
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_mode & 0o077
                or info.st_size > MAX_DESKTOP_BYTES
            ):
                raise AutostartError("unsafe_autostart_file")
            with os.fdopen(os.dup(fd), "rb") as reader:
                raw = reader.read(MAX_DESKTOP_BYTES + 1)
            if len(raw) > MAX_DESKTOP_BYTES:
                raise AutostartError("autostart_file_too_large")
            after = path.lstat()
            if (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
            ):
                raise AutostartError("autostart_file_changed")
            return raw
        finally:
            os.close(fd)
    except (OSError, UnicodeError):
        raise AutostartError("autostart_file_unavailable") from None


def inspect_autostart(*, config_root: str | Path | None = None) -> AutostartStatus:
    root = _root(config_root)
    try:
        root.lstat()
    except FileNotFoundError:
        return AutostartStatus("disabled")
    except OSError:
        raise AutostartError("config_root_unavailable") from None
    _check_directory(root)
    parent = root / "autostart"
    try:
        parent.lstat()
    except FileNotFoundError:
        return AutostartStatus("disabled")
    except OSError:
        raise AutostartError("autostart_directory_unavailable") from None
    _check_directory(parent)
    target = parent / DESKTOP_NAME
    try:
        target.lstat()
    except FileNotFoundError:
        return AutostartStatus("disabled")
    except OSError:
        raise AutostartError("autostart_file_unavailable") from None
    raw = _existing_bytes(target)
    return AutostartStatus(
        "enabled" if _is_isc_desktop(raw) else "unmanaged"
    )


def modify_autostart(
    action: str, *, config_root: str | Path | None = None,
) -> AutostartStatus:
    """Change only the exact ISC-owned .desktop entry after explicit consent."""
    if action not in ("enable", "disable"):
        raise AutostartError("unsupported_autostart_action")
    root = _root(config_root)
    current = inspect_autostart(config_root=root)
    if current.state == "unmanaged":
        raise AutostartError("existing_startup_entry_not_owned")
    if action == "disable":
        if current.state == "disabled":
            return AutostartStatus("disabled")
        parent = root / "autostart"
        target = parent / DESKTOP_NAME
        if not _is_isc_desktop(_existing_bytes(target)):
            raise AutostartError("startup_entry_changed")
        try:
            target.unlink()
            _sync_directory(parent)
        except OSError:
            raise AutostartError("unable_to_remove_startup_entry") from None
        return AutostartStatus("disabled", changed=True)
    if current.state == "enabled":
        current_bytes = _existing_bytes(root / "autostart" / DESKTOP_NAME)
        if current_bytes != desktop_contents():
            raise AutostartError("autostart_path_migration_requires_disable_first")
        return AutostartStatus("enabled")

    # Enabling requires a user-owned, non-group-writable XDG config root.
    # We do not silently create or repair the parent configuration root.
    _check_directory(root)
    parent = root / "autostart"
    try:
        parent.mkdir(mode=0o700, exist_ok=True)
        _check_directory(parent)
        fd, path = tempfile.mkstemp(prefix=".isc-autostart-", dir=parent)
        staged = Path(path)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(os.dup(fd), "wb") as output:
                output.write(desktop_contents())
                output.flush()
            os.fsync(fd)
        finally:
            os.close(fd)
        try:
            # Hardlink publishes only if target is still absent; never replace.
            os.link(staged, parent / DESKTOP_NAME)
            _sync_directory(parent)
        finally:
            staged.unlink(missing_ok=True)
    except (OSError, AutostartError):
        raise AutostartError("unable_to_create_startup_entry") from None
    return AutostartStatus("enabled", changed=True)


def _sync_directory(parent: Path) -> None:
    fd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
