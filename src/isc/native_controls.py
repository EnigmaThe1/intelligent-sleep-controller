"""Linux-native power-profile controls and system-settings launcher."""
from __future__ import annotations
import os
import re
import secrets
import subprocess
from dataclasses import dataclass

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
        )
        if name in os.environ
    }
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


def display_support():
    session = os.environ.get("XDG_SESSION_TYPE", "").lower()
    if session == "wayland":
        desktop = os.environ.get("XDG_CURRENT_DESKTOP", "")
        # Do not touch other compositors, even if they expose XWayland.
        if "gnome" not in desktop.lower().split(":"):
            return False, "requires_gnome_wayland"
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
    if session != "x11":
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


def _schedule_wayland_display_restore():
    """Arm a distinct user-systemd recovery timer BEFORE blanking displays.

    The timer is independent of the ISC tray/window process. It requests
    MUTTER ON after twenty seconds. Repeated user actions have independent
    recovery timers and may therefore wake earlier than the latest request.
    """
    unit = "isc-display-restore-" + secrets.token_hex(8)
    recovery = [
        "systemd-run", "--user", "--quiet",
        "--unit=" + unit,
        "--on-active=" + str(_WAYLAND_RESTORE_SECONDS) + "s",
        "--timer-property=AccuracySec=1s",
        "/usr/bin/busctl", "--user", "set-property",
        *_MUTTER_PROPERTY, "i", "0",
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
    if reason == "wayland_mutter_standby_available":
        # No direct Wayland power-state command is attempted until systemd
        # has independently accepted and activated its recovery timer.
        try:
            armed = _schedule_wayland_display_restore()
        except (NativeControlError, OSError, ValueError, RuntimeError):
            armed = False
        if not armed:
            raise NativeControlError("wayland_recovery_timer_unavailable")
        # Recheck for a concurrent compositor/session change.
        try:
            state = _mutter_display_mode()
        except NativeControlError:
            state = None
        if state != 0:
            raise NativeControlError("mutter_display_changed_before_standby")
        if _run(["busctl", "--user", "set-property", *_MUTTER_PROPERTY,
                 "i", "1"]).returncode:
            raise NativeControlError("mutter_standby_request_failed")
        return "display_standby_requested_with_20s_recovery"
    raise NativeControlError("unsupported_display_session")


def open_power_settings():
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        raise NativeControlError("no_graphical_session")
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
            ["gnome-control-center", "power"],
            env=safe_env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
    except OSError:
        raise NativeControlError("gnome_power_settings_unavailable") from None
