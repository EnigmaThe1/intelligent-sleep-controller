"""Read-only ISC CLI. Never invokes systemctl suspend or changes settings."""

from __future__ import annotations

import argparse
import sys
import subprocess
from typing import Sequence

from . import __version__
from .gnome import read_gnome_idle_ms
from .hook_payload import read_hook_payload, SUPPORTED_SOURCES
from .hook_plan import build_hook_plan, default_hook_targets, UnsafeHookConfiguration
from .inhibitors import read_inhibitors
from .policy import Snapshot, WorkItem, WorkState, evaluate
from .processes import read_process_candidates
from .receiver import LocalMonitor, send_observation, send_discovered_observation


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


def processes() -> int:
    """List candidate process names/PIDs without asserting workload activity."""
    scan = read_process_candidates()
    if scan is None:
        print("Linux process candidate inventory: UNKNOWN")
        print("ISC cannot assume unobserved processes are idle.")
    else:
        print(f"Linux process candidates (current user only): {scan.count}")
        for item in scan.candidates[:40]:
            print(f"  pid={item.pid} name={item.name} state=UNKNOWN")
        if scan.count > 40:
            print(f"  ... {scan.count - 40} more candidates not shown")
        print("This inventory is illustrative, not exhaustive.")
        print("A process name does NOT prove task activity or completion.")
    print("ISC automatic suspend: NOT IMPLEMENTED")
    print("No system configuration has been changed.")
    return 0


def check() -> int:
    """Explain a conservative real-host readiness check without permitting sleep."""
    idle_ms = read_gnome_idle_ms()
    locks = read_inhibitors()
    scan = read_process_candidates()
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
            work_items=tuple(
                WorkItem(
                    identifier=f"process_{candidate.pid}",
                    state=WorkState.UNKNOWN,
                )
                for candidate in (scan.candidates if scan is not None else ())
            ),
        )
    )
    print("ISC READ-ONLY SAFETY CHECK")
    print(f"GNOME idle: {_format_idle_ms(idle_ms)}")
    print("Idle threshold: 1800 seconds")
    if scan is None:
        print("Process candidate inventory: UNKNOWN")
    else:
        print(f"Process candidates requiring lifecycle verification: {scan.count}")
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


def hook_probe(source: str) -> int:
    """Inspect supplied hook JSON; never installs a hook or stores an event."""
    observation = read_hook_payload(source, sys.stdin.buffer)
    print("ISC HOOK PAYLOAD PROBE (READ-ONLY)")
    if observation is None:
        print("Observation: UNKNOWN (missing, malformed, or oversized input)")
    else:
        print(f"Source: {observation.source}")
        print(f"Event: {observation.event_name}")
        print(f"Conservative interpretation: {observation.state.value}")
        print("Session identifier: fingerprinted in memory, not displayed")
    print("Hook event origin and child-work quiescence: NOT VERIFIED")
    print("No hook configuration has been modified.")
    print("ISC automatic suspend: NOT IMPLEMENTED")
    return 0


def monitor(*, seconds: float, self_test: bool = False, advertise: bool = False) -> int:
    """Ephemeral foreground monitor; creates only a private runtime socket."""
    import json

    try:
        with LocalMonitor(advertise=advertise) as listener:
            print("ISC FOREGROUND MONITOR (NO SUSPEND)")
            print(f"Private Unix socket: {listener.path}", flush=True)
            if self_test:
                raw = json.dumps({
                    "session_id": "synthetic-monitor-session",
                    "hook_event_name": "Stop",
                }).encode()
                from .hook_payload import parse_hook_payload
                observation = parse_hook_payload("codex", raw)
                delivered = (
                    send_discovered_observation(observation) if advertise
                    else send_observation(listener.path, observation)
                ) if observation is not None else False
                if not delivered:
                    print("Synthetic test event could not be queued.")
                    return 2
            listener.run_for(seconds)
            print(f"Accepted metadata events: {listener.accepted}")
            print(f"Rejected metadata events: {listener.rejected}")
            print(f"Conservative active events: {listener.counts['active']}")
            print(f"Conservative unknown events: {listener.counts['unknown']}")
            print("Full workload coverage: NOT VERIFIED")
            print("ISC automatic suspend: NOT IMPLEMENTED")
            print("No services, hooks, or power settings were modified.")
        print("Private Unix socket cleaned up.")
        return 0
    except (OSError, PermissionError, ValueError) as error:
        print(f"Monitor unavailable: {type(error).__name__}")
        print("ISC automatic suspend: NOT IMPLEMENTED")
        return 2


def send_hook(*, source: str, socket_path: str | None = None) -> int:
    """Optional future hook entry; fail-open and silent by design.

    This is not installed automatically into Claude Code or Codex.
    """
    observation = read_hook_payload(source, sys.stdin.buffer)
    if observation is not None:
        if socket_path is None:
            send_discovered_observation(observation)
        else:
            send_observation(socket_path, observation)
    return 0


def hook_plan() -> int:
    """Only show a minimal integration summary; never output commands or secrets."""
    print("ISC HOOK INTEGRATION PLAN — READ ONLY")
    rc = 0
    for source, target in default_hook_targets():
        print(f"{source}:")
        try:
            plan = build_hook_plan(source, target)
        except (ValueError, UnsafeHookConfiguration):
            print("  configuration: UNAVAILABLE OR UNSUPPORTED")
            print("  action: NONE")
            rc = 2
            continue
        print(f"  existing configured event types: {len(plan.configured_events)}")
        print(f"  ISC hook events already present: {len(plan.already_installed)}")
        print(f"  ISC hook event groups proposed: {len(plan.proposed_events)}")
        for event in plan.proposed_events:
            print(f"    + {event}")
        print("  no existing hook commands or settings displayed")
    print("No files modified. No hook commands executed.")
    print("ISC automatic suspend: NOT IMPLEMENTED")
    return rc


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="smart-sleep",
        description="Read-only foundation for future intelligent Linux sleep management",
    )
    parser.add_argument(
        "command", nargs="?",
        choices=("status", "idle", "inhibitors", "processes", "check", "hook-probe", "monitor", "send-hook", "hook-plan"),
        default="status",
    )
    parser.add_argument(
        "--source", choices=sorted(SUPPORTED_SOURCES),
        help="Source application for the read-only hook-probe command",
    )
    parser.add_argument("--seconds", type=float, default=1.0, help="Foreground monitor runtime, max 300 seconds")
    parser.add_argument("--self-test", action="store_true", help="Send one synthetic Stop event to the private monitor")
    parser.add_argument("--advertise", action="store_true", help="Expose temporary receiver via a fixed private runtime socket; never changes agent hooks")
    parser.add_argument("--socket-path", help="Explicit local Unix socket path for send-hook")
    args = parser.parse_args(argv)
    if args.command == "status":
        return status()
    if args.command == "idle":
        return idle()
    if args.command == "inhibitors":
        return inhibitors()
    if args.command == "processes":
        return processes()
    if args.command == "check":
        return check()
    if args.command == "hook-probe":
        if args.source is None:
            parser.error("hook-probe requires --source")
        return hook_probe(args.source)
    if args.command == "monitor":
        return monitor(seconds=args.seconds, self_test=args.self_test, advertise=args.advertise)
    if args.command == "hook-plan":
        return hook_plan()
    if args.command == "send-hook":
        if args.source is None:
            parser.error("send-hook requires --source")
        return send_hook(source=args.source, socket_path=args.socket_path)
    return 2
