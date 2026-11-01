"""Read-only ISC CLI. Never invokes systemctl suspend or changes settings."""

from __future__ import annotations

import argparse
import subprocess
from typing import Sequence

from . import __version__
from .gnome import read_gnome_idle_ms


def _probe(argv: list[str]) -> str:
    try:
        done = subprocess.run(
            argv, capture_output=True, text=True, timeout=3, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unavailable"
    if done.returncode:
        return "unavailable"
    return done.stdout.strip() or "unavailable"


def _idle_text() -> str:
    idle_ms = read_gnome_idle_ms()
    if idle_ms is None:
        return "unknown (GNOME idle monitor unavailable)"
    return f"{idle_ms / 1000:.3f} seconds ({idle_ms} ms)"


def status() -> int:
    print(f"Intelligent Sleep Controller {__version__}")
    print("Implementation: foundation (read-only)")
    print("ISC automatic suspend: NOT IMPLEMENTED")
    print("ISC service installation: NOT IMPLEMENTED")
    print("ISC operator controls ON/OFF/MONITOR: PLANNED")
    try:
        with open("/sys/power/mem_sleep", encoding="utf-8") as stream:
            power_modes = stream.read().strip()
    except OSError:
        power_modes = "unavailable"
    print(f"Kernel suspend modes: {power_modes}")
    print(
        "GNOME AC suspend timeout: "
        + _probe(
            [
                "gsettings",
                "get",
                "org.gnome.settings-daemon.plugins.power",
                "sleep-inactive-ac-timeout",
            ]
        )
    )
    print("GNOME user idle: " + _idle_text())
    print("No system configuration has been changed.")
    return 0


def idle() -> int:
    print("GNOME user idle: " + _idle_text())
    print("User idle time is NOT proof that background work has stopped.")
    print("ISC automatic suspend: NOT IMPLEMENTED")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="smart-sleep",
        description="Read-only foundation for future intelligent Linux sleep management",
    )
    parser.add_argument(
        "command", nargs="?", choices=("status", "idle"), default="status"
    )
    args = parser.parse_args(argv)
    if args.command == "status":
        return status()
    if args.command == "idle":
        return idle()
    return 2
