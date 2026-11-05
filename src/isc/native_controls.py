"""Linux-native power-profile controls and system-settings launcher."""
from __future__ import annotations
import os
import re
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
    try:
        return subprocess.run(args, check=False, text=True, capture_output=True, timeout=5)
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

def display_support():
    if os.environ.get("XDG_SESSION_TYPE", "").lower() != "x11":
        return False, "requires_xorg"
    if not os.environ.get("DISPLAY"):
        return False, "no_display"
    try:
        result = _run(["xset", "q"])
    except NativeControlError:
        return False, "xset_unavailable"
    if result.returncode or not re.search(r"(?m)^\s*DPMS is Enabled\s*$", result.stdout):
        return False, "dpms_not_available"
    return True, "x11_dpms_available"

def display_off(*, confirm=False):
    if not confirm:
        return "confirmation_required"
    supported, reason = display_support()
    if not supported:
        raise NativeControlError(reason)
    if _run(["xset", "dpms", "force", "off"]).returncode:
        raise NativeControlError("display_request_failed")
    return "display_off_requested"

def open_power_settings():
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        raise NativeControlError("no_graphical_session")
    try:
        subprocess.Popen(["gnome-control-center", "power"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    except OSError:
        raise NativeControlError("gnome_power_settings_unavailable") from None
