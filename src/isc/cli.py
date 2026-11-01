"""Read-only ISC CLI. Never invokes systemctl suspend or changes settings."""

from __future__ import annotations

import argparse
import subprocess
from typing import Sequence

from . import __version__
from .gnome import read_gnome_idle_ms
from .inhibitors import read_inhibitors
from .policy import Snapshot, evaluate


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


def inhibitors() -> int:
    locks = read_inhibitors()
    if locks is None:
        print("systemd-logind inhibitors: UNKNOWN (read failed or malformed)")
        print("ISC cannot assume no sleep blockers are present.")
    else:
        blocking = sum(lock.blocks_automatic_sleep for lock in locks)
        delaying = sum(lock.delays_sleep for lock in locks)
        other = len(locks) - blocking - delaying
        print(f"systemd-logind inhibitors: {len(locks)}")
        print(f"Blocking automatic sleep/idle: {blocking}")
        print(f"Sleep delays (not permanent blockers): {delaying}")
        print(f"Other inhibitor locks: {other}")
        print("GNOME session inhibitors and unrelated workloads: NOT YET VERIFIED")
    print("ISC automatic suspend: NOT IMPLEMENTED")
    print("No system configuration has been changed.")
    return 0


def check() -> int:
    """Explain a conservative real-host readiness check without permitting sleep."""
    idle_ms = read_gnome_idle_ms()
    locks = read_inhibitors()
    blockers: tuple[str, ...]
    if locks is None:
        blockers = ("logind_inhibitors_unverified",)
    else:
        blockers = tuple(
            f"logind_blocker_{index}"
            for index, lock in enumerate(locks)
            if lock.blocks_automatic_sleep
        )

    # There is not yet an authoritative supervisor or timer/wake verifier.
    # Both must remain False even when no running jobs are discovered.
    result = evaluate(
        Snapshot(
            user_idle_seconds=(idle_ms / 1000) if idle_ms is not None else None,
            threshold_seconds=1800,
            active_sleep_inhibitors=blockers,
            coverage_complete=False,
            scheduled_work_safe=False,
        )
    )
    print("ISC READ-ONLY SAFETY CHECK")
    print(f"GNOME idle: {_format_idle_ms(idle_ms)}")
    print("Idle threshold: 1800 seconds")
    print("Application workload coverage: NOT VERIFIED")
    print("Scheduled work and wake safety: NOT VERIFIED")
    print("GNOME session inhibitors: NOT VERIFIED")
    print("Verdict: DO NOT AUTO-SUSPEND")
    for reason in result.reasons:
        print(f"  - {reason}")
    print("ISC automatic suspend: NOT IMPLEMENTED")
    print("No system configuration has been changed.")
    return 0


def _format_idle_ms(idle_ms: int | None) -> str:
    if idle_ms is None:
        return "unknown"
    return f"{idle_ms / 1000:.3f} seconds"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="smart-sleep",
        description="Read-only foundation for future intelligent Linux sleep management",
    )
    parser.add_argument(
        "command", nargs="?", choices=("status", "idle", "inhibitors", "check"), default="status"
    )
    args = parser.parse_args(argv)
    if args.command == "status":
        return status()
    if args.command == "idle":
        return idle()
    if args.command == "inhibitors":
        return inhibitors()
    if args.command == "check":
        return check()
    return 2
