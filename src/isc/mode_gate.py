"""One shared, fail-closed ISC operating-mode decision for version 1.0.

No power actions. ON stays disarmed even if a future/corrupt setting supplies
it. OFF must never be interpreted as permission to launch a MONITOR receiver.
"""
from __future__ import annotations

from dataclasses import dataclass

from .settings import ModeConfigError, ModeStatus, read_mode


@dataclass(frozen=True)
class EffectiveMode:
    mode: str
    monitor_allowed: bool
    automatic_suspend_allowed: bool = False
    configuration_valid: bool = False
    reason: str = "invalid_configuration"


def resolve_mode(status: ModeStatus | None) -> EffectiveMode:
    """Single explicit gate usable by tray, foreground and user service."""
    if not isinstance(status, ModeStatus) or type(status.mode) is not str:
        return EffectiveMode("off", False)
    if status.mode == "monitor":
        return EffectiveMode("monitor", True, configuration_valid=True,
                             reason="monitor_selected")
    if status.mode == "off":
        return EffectiveMode("off", False, configuration_valid=True,
                             reason="off_selected")
    # ON is intentionally not supported by current persistent settings.
    return EffectiveMode("off", False, reason="unsupported_mode")


def current_mode() -> EffectiveMode:
    try:
        return resolve_mode(read_mode())
    except (ModeConfigError, OSError, ValueError):
        return EffectiveMode("off", False)
