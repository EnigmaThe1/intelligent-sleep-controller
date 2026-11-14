"""Identify the current Linux display session without guessing its power API.

X11 is a display-server protocol, GNOME/Plasma are desktop environments,
and Wayland alone does not define a universal monitor-standby command.
A compositor-specific backend must be explicitly supported and probed.
No subprocess, desktop or power action occurs in this module.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Mapping


@dataclass(frozen=True)
class DesktopPlatform:
    session: str
    desktop: str
    display_backend: str


def detect_desktop_platform(
    environment: Mapping[str, str] | None = None,
) -> DesktopPlatform:
    env = os.environ if environment is None else environment
    session = env.get("XDG_SESSION_TYPE", "").strip().lower()
    tokens = {
        part.strip().lower()
        for part in (
            env.get("XDG_CURRENT_DESKTOP")
            or env.get("XDG_SESSION_DESKTOP") or ""
        ).split(":")
        if part.strip()
    }
    gnome = "gnome" in tokens
    kde = bool(tokens & {"kde", "plasma"})
    desktop = (
        "ambiguous" if gnome and kde else
        "GNOME" if gnome else "KDE Plasma" if kde else
        "other" if tokens else "unknown"
    )
    # X11 DPMS is desktop-neutral: XFCE, Cinnamon, GNOME, KDE, etc.
    if session == "x11":
        backend = "x11"
    elif session == "wayland" and gnome and not kde:
        backend = "gnome-wayland"
    elif session == "wayland" and kde and not gnome:
        backend = "kde-wayland"
    else:
        backend = "unsupported"
    return DesktopPlatform(session or "unknown", desktop, backend)
