"""Linux-native power-profile controls and system-settings launcher."""
from __future__ import annotations
import os
import re
import secrets
import shutil
import subprocess
from dataclasses import dataclass

from .desktop_platform import detect_desktop_platform

PROFILES = ("power-saver", "balanced", "performance")
class NativeControlError(RuntimeError):
    pass

@dataclass(frozen=True)
class PowerProfiles:
    current: str
    available: tuple[str, ...]

def _run(args):
    # Do not forward unrelated agent tokens or application secrets.
    env = {
        name: os.environ[name]
        for name in (
            "PATH", "HOME", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS",
            "LANG", "LC_ALL", "TZ", "DISPLAY", "XAUTHORITY",
            "WAYLAND_DISPLAY", "XDG_SESSION_TYPE", "XDG_CURRENT_DESKTOP",
        )
        if name in os.environ
    }
    # KScreen requires the real Wayland socket even if XWayland DISPLAY exists.
    # Never pass unrelated QT_* variables or private application environment.
    if (
        args and os.path.basename(args[0]) == "kscreen-doctor"
        and detect_desktop_platform().display_backend == "kde-wayland"
    ):
        env["QT_QPA_PLATFORM"] = "wayland"
    try:
        return subprocess.run(
            args, env=env, check=False, text=True,
            capture_output=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise NativeControlError("native_command_unavailable") from None

def power_profiles():
    available = _run(["powerprofilesctl", "list"])
    selected = _run(["powerprofilesctl", "get"])
    if available.returncode or selected.returncode:
        raise NativeControlError("profile_service_unavailable")
    names = tuple(name for name in PROFILES if re.search(r"(?m)^\s*\*?\s*" + re.escape(name) + r":\s*$", available.stdout))
    current = selected.stdout.strip()
    if not names or current not in names:
        raise NativeControlError("profile_state_unknown")
    return PowerProfiles(current, names)

def set_profile(name):
    if name not in PROFILES:
        raise NativeControlError("invalid_profile")
    before = power_profiles()
    if name not in before.available:
        raise NativeControlError("unsupported_profile_on_host")
    if before.current == name:
        return before
    if _run(["powerprofilesctl", "set", name]).returncode:
        raise NativeControlError("profile_change_refused")
    after = power_profiles()
    if after.current != name:
        raise NativeControlError("profile_change_unverified")
    return after

# Mutter uses this session-bus property for display power states on GNOME
# Wayland. 0=on, 1=standby; -1 means unsupported. No host suspend.
_MUTTER_PROPERTY = (
    "org.gnome.Mutter.DisplayConfig",
    "/org/gnome/Mutter/DisplayConfig",
    "org.gnome.Mutter.DisplayConfig",
    "PowerSaveMode",
)
_WAYLAND_RESTORE_SECONDS = 20


def _mutter_display_mode():
    """Read the GNOME compositor's display state; never change it."""
    result = _run(["busctl", "--user", "get-property", *_MUTTER_PROPERTY])
    if result.returncode:
        return None
    match = re.fullmatch(r"i (-?\d+)\s*", result.stdout)
    if match is None:
        return None
    value = int(match.group(1))
    return value if value in (-1, 0, 1, 2, 3) else None


def _kscreen_doctor():
    """Resolve the user's actual KDE utility, not a hard-coded distro path."""
    path = shutil.which("kscreen-doctor")
    if (
        not isinstance(path, str) or not os.path.isabs(path)
        or not re.fullmatch(r"/[A-Za-z0-9_./-]+", path)
    ):
        return None
    return path


def _kde_dpms_modes():
    """Require a nonempty, fully understood KScreen per-output report."""
    utility = _kscreen_doctor()
    if utility is None or not os.environ.get("WAYLAND_DISPLAY"):
        return None
    result = _run([utility, "--dpms", "show"])
    if result.returncode:
        return None
    output = result.stdout.splitlines()
    if not output or len(output) > 16:
        return None
    modes = []
    for line in output:
        match = re.fullmatch(
            r"dpms mode for screen [A-Za-z0-9_.:-]{1,100}: (on|off)",
            line.strip(),
        )
        if match is None:
            return None
        modes.append(match.group(1))
    return tuple(modes)


def display_support():
    """Choose a tested display backend, then require actual native capability."""
    backend = detect_desktop_platform().display_backend
    if backend == "gnome-wayland":
        try:
            state = _mutter_display_mode()
        except NativeControlError:
            return False, "mutter_display_unavailable"
        if state == 0:
            return True, "wayland_mutter_standby_available"
        return False, (
            "mutter_display_unavailable" if state is None or state == -1
            else "mutter_display_not_on"
        )
    if backend == "kde-wayland":
        try:
            modes = _kde_dpms_modes()
        except NativeControlError:
            modes = None
        if modes is None:
            return False, "kscreen_dpms_unavailable"
        return (
            (True, "wayland_kscreen_dpms_available")
            if all(mode == "on" for mode in modes)
            else (False, "kscreen_display_not_on")
        )
    if backend != "x11":
        return False, "unsupported_display_session"
    if not os.environ.get("DISPLAY"):
        return False, "no_display"
    try:
        result = _run(["xset", "q"])
    except NativeControlError:
        return False, "xset_unavailable"
    if result.returncode or not re.search(r"(?m)^\s*DPMS is Enabled\s*$", result.stdout):
        return False, "dpms_not_available"
    return True, "x11_dpms_available"


def _schedule_wayland_display_restore(backend="gnome-wayland"):
    """Arm a distinct, active user-systemd restore timer BEFORE standby.

    GNOME uses Mutter's session D-Bus property; KDE uses its installed
    Wayland KScreen utility. No timer is considered verified until systemd
    reports it active. Timer expiry is an attempted wake, never a guarantee.
    """
    environment = []
    if backend == "gnome-wayland":
        command = [
            "/usr/bin/busctl", "--user", "set-property",
            *_MUTTER_PROPERTY, "i", "0",
        ]
    elif backend == "kde-wayland":
        utility = _kscreen_doctor()
        socket = os.environ.get("WAYLAND_DISPLAY", "")
        # Strict socket token: do not pass arbitrary strings to systemd-run.
        if (
            utility is None or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", socket)
        ):
            return False
        command = [utility, "--dpms", "on"]
        environment = [
            "--setenv=WAYLAND_DISPLAY=" + socket,
            "--setenv=QT_QPA_PLATFORM=wayland",
        ]
    else:
        return False
    unit = "isc-display-restore-" + secrets.token_hex(8)
    recovery = [
        "systemd-run", "--user", "--quiet",
        "--unit=" + unit,
        "--on-active=" + str(_WAYLAND_RESTORE_SECONDS) + "s",
        "--timer-property=AccuracySec=1s",
        *environment, *command,
    ]
    result = _run(recovery)
    if result.returncode:
        return False
    timer = _run(["systemctl", "--user", "is-active", unit + ".timer"])
    return timer.returncode == 0 and timer.stdout.strip() == "active"


def display_off(*, confirm=False):
    if not confirm:
        return "confirmation_required"
    supported, reason = display_support()
    if not supported:
        raise NativeControlError(reason)
    if reason == "x11_dpms_available":
        if _run(["xset", "dpms", "force", "off"]).returncode:
            raise NativeControlError("display_request_failed")
        return "display_off_requested"
    if reason in (
        "wayland_mutter_standby_available", "wayland_kscreen_dpms_available",
    ):
        # Never blank a screen before the independent recovery timer exists.
        backend = (
            "gnome-wayland" if reason == "wayland_mutter_standby_available"
            else "kde-wayland"
        )
        try:
            armed = _schedule_wayland_display_restore(backend)
        except (NativeControlError, OSError, ValueError, RuntimeError):
            armed = False
        if not armed:
            raise NativeControlError("wayland_recovery_timer_unavailable")
        if detect_desktop_platform().display_backend != backend:
            raise NativeControlError("display_session_changed_before_standby")
        if backend == "gnome-wayland":
            try:
                state = _mutter_display_mode()
            except NativeControlError:
                state = None
            if state != 0:
                raise NativeControlError("mutter_display_changed_before_standby")
            command = [
                "busctl", "--user", "set-property", *_MUTTER_PROPERTY, "i", "1",
            ]
        else:
            try:
                modes = _kde_dpms_modes()
            except NativeControlError:
                modes = None
            if not modes or not all(mode == "on" for mode in modes):
                raise NativeControlError("kscreen_display_changed_before_standby")
            utility = _kscreen_doctor()
            if utility is None:
                raise NativeControlError("kscreen_dpms_unavailable")
            command = [utility, "--dpms", "off"]
        if _run(command).returncode:
            raise NativeControlError(
                "mutter_standby_request_failed"
                if backend == "gnome-wayland"
                else "kscreen_standby_request_failed"
            )
        return "display_standby_requested_with_20s_recovery"
    raise NativeControlError("unsupported_display_session")


def open_power_settings():
    """Open the current desktop's settings; never launch GNOME on Plasma."""
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        raise NativeControlError("no_graphical_session")
    desktop = detect_desktop_platform().desktop
    if desktop == "GNOME":
        command = ["gnome-control-center", "power"]
    elif desktop == "KDE Plasma":
        # Plasma's System Settings contains the Power Management KCM.
        command = ["systemsettings"]
    else:
        raise NativeControlError("desktop_power_settings_unsupported")
    try:
        safe_env = {
            key: os.environ[key]
            for key in (
                "HOME", "DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "PATH",
                "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS",
                "XDG_CURRENT_DESKTOP", "XDG_SESSION_TYPE",
                "LANG", "LC_ALL", "GDK_BACKEND",
            )
            if key in os.environ
        }
        subprocess.Popen(
            command,
            env=safe_env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
    except OSError:
        raise NativeControlError("desktop_power_settings_unavailable") from None
