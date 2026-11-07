"""Explicit, reversible GNOME application-menu launcher for ISC.

Different from optional login autostart (which uses NoDisplay=true).
Install/remove is a single user-owned desktop entry, never the monitor
service or GNOME power settings. Launching the desktop entry opens the
visible ISC window; a tray remains an optional independent process.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import stat
import sys
import tempfile

LAUNCHER_NAME = "intelligent-sleep-controller.desktop"
MAX_LAUNCHER_BYTES = 4096
_SAFE_PATH = re.compile(r"/[A-Za-z0-9_/.-]+\Z", re.ASCII)


class DesktopLauncherError(ValueError):
    """Fixed diagnostic codes only; no existing file content is exposed."""


@dataclass(frozen=True)
class LauncherStatus:
    state: str  # missing | installed | outdated | foreign
    changed: bool = False


def launcher_contents(
    *, interpreter: str | None = None, source_root: str | Path | None = None,
) -> bytes:
    """Freedesktop desktop entry launching a visible window without shell."""
    exe = str(interpreter or sys.executable)
    src = str(source_root or Path(__file__).resolve().parents[1])
    if not _SAFE_PATH.fullmatch(exe) or not _SAFE_PATH.fullmatch(src):
        raise DesktopLauncherError("unsupported_launcher_path")
    return (
        "[Desktop Entry]\n"
        "Version=1.0\n"
        "Type=Application\n"
        "Name=Intelligent Sleep Controller\n"
        "GenericName=Power Management\n"
        "Comment=Local sleep monitoring and manual power controls\n"
        "Icon=preferences-system-power\n"
        "Categories=Settings;Utility;\n"
        f"TryExec={exe}\n"
        "Exec=/usr/bin/env PYTHONDONTWRITEBYTECODE=1 "
        f"PYTHONPATH={src} {exe} -B -m isc window\n"
        "Terminal=false\n"
        "StartupNotify=true\n"
        "X-ISC-Managed=desktop-launcher-v1\n"
    ).encode("utf-8")


def _owned(raw: bytes) -> bool:
    """Exact structure, including legacy interpreter/source path migration."""
    try:
        text = raw.decode("utf-8")
    except UnicodeError:
        return False
    exe = re.search(r"(?m)^TryExec=(/[A-Za-z0-9_/.-]+)$", text)
    entry = re.search(
        r"(?m)^Exec=/usr/bin/env PYTHONDONTWRITEBYTECODE=1 "
        r"PYTHONPATH=(/[A-Za-z0-9_/.-]+) "
        r"(/[A-Za-z0-9_/.-]+) -B -m isc window$", text,
    )
    if exe is None or entry is None or exe.group(1) != entry.group(2):
        return False
    try:
        return raw == launcher_contents(
            interpreter=exe.group(1), source_root=entry.group(1),
        )
    except DesktopLauncherError:
        return False


def _data_root(data_root: str | Path | None) -> Path:
    base = data_root or os.environ.get("XDG_DATA_HOME") or (
        Path.home() / ".local" / "share"
    )
    root = Path(base)
    if not root.is_absolute():
        raise DesktopLauncherError("xdg_data_home_must_be_absolute")
    return root


def _safe_directory(path: Path) -> None:
    try:
        st = path.lstat()
    except OSError:
        raise DesktopLauncherError("launcher_directory_unavailable") from None
    if (
        not stat.S_ISDIR(st.st_mode)
        or st.st_uid != os.getuid()
        or st.st_mode & 0o022
    ):
        raise DesktopLauncherError("unsafe_launcher_directory")


def _read_file(path: Path) -> bytes:
    fd = None
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        st = os.fstat(fd)
        if (
            not stat.S_ISREG(st.st_mode)
            or st.st_uid != os.getuid()
            or st.st_mode & 0o022
            or st.st_size > MAX_LAUNCHER_BYTES
        ):
            raise DesktopLauncherError("unsafe_launcher_file")
        with os.fdopen(os.dup(fd), "rb") as stream:
            raw = stream.read(MAX_LAUNCHER_BYTES + 1)
        after = path.lstat()
        if (
            len(raw) > MAX_LAUNCHER_BYTES
            or (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        ):
            raise DesktopLauncherError("launcher_changed_during_read")
        return raw
    except OSError:
        raise DesktopLauncherError("launcher_unavailable_or_unsafe") from None
    finally:
        if fd is not None:
            os.close(fd)


def inspect_launcher(*, data_root: str | Path | None = None) -> LauncherStatus:
    root = _data_root(data_root)
    if not root.exists() and not root.is_symlink():
        return LauncherStatus("missing")
    _safe_directory(root)
    apps = root / "applications"
    if not apps.exists() and not apps.is_symlink():
        return LauncherStatus("missing")
    _safe_directory(apps)
    path = apps / LAUNCHER_NAME
    if not path.exists() and not path.is_symlink():
        return LauncherStatus("missing")
    raw = _read_file(path)
    if not _owned(raw):
        return LauncherStatus("foreign")
    return LauncherStatus(
        "installed" if raw == launcher_contents() else "outdated"
    )


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _prepare_root(root: Path) -> None:
    """Create missing default XDG directories; never touch foreign entries."""
    # Validate existing path components without traversing a symlink.
    parent = root.parent
    _safe_directory(parent)
    if not root.exists() and not root.is_symlink():
        root.mkdir(mode=0o755)
    _safe_directory(root)


def modify_launcher(
    action: str, *, data_root: str | Path | None = None,
) -> LauncherStatus:
    """Explicit install/remove only; no service, process or autostart action."""
    if action not in ("install", "remove"):
        raise DesktopLauncherError("invalid_launcher_action")
    root = _data_root(data_root)
    existing = inspect_launcher(data_root=root)
    if existing.state == "foreign":
        raise DesktopLauncherError("foreign_launcher_not_modified")
    if action == "remove":
        if existing.state == "missing":
            return existing
        apps = root / "applications"
        target = apps / LAUNCHER_NAME
        if not _owned(_read_file(target)):
            raise DesktopLauncherError("launcher_changed_before_removal")
        try:
            target.unlink()
            _fsync_dir(apps)
        except OSError:
            raise DesktopLauncherError("launcher_removal_failed") from None
        return LauncherStatus("missing", changed=True)
    if existing.state == "installed":
        return existing
    if existing.state == "outdated":
        raise DesktopLauncherError("outdated_launcher_remove_before_reinstall")
    try:
        _prepare_root(root)
        apps = root / "applications"
        if not apps.exists() and not apps.is_symlink():
            apps.mkdir(mode=0o755)
        _safe_directory(apps)
        fd, temp = tempfile.mkstemp(prefix=".isc-launcher-", dir=apps)
        staged = Path(temp)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(os.dup(fd), "wb") as output:
                output.write(launcher_contents())
                output.flush()
            os.fsync(fd)
        finally:
            os.close(fd)
        try:
            os.link(staged, apps / LAUNCHER_NAME)
            _fsync_dir(apps)
        finally:
            staged.unlink(missing_ok=True)
    except (OSError, DesktopLauncherError):
        raise DesktopLauncherError("launcher_install_failed") from None
    return LauncherStatus("installed", changed=True)
