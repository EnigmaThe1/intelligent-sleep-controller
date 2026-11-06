"""ISC CLI. Only an explicit, interactive manual command can request suspend."""

from __future__ import annotations

import argparse
import sys
import subprocess
import time
from typing import Sequence

from . import __version__
from .gnome import read_gnome_idle_ms
from .hook_payload import read_hook_payload, SUPPORTED_SOURCES
from .hook_diagnostics import check_hook_delivery
from .hook_plan import build_hook_plan, default_hook_targets, UnsafeHookConfiguration
from .hook_install import HookInstallError, modify_existing_hooks
from .inhibitors import read_inhibitors
from .policy import Snapshot, WorkItem, WorkState, evaluate
from .processes import read_process_candidates
from .receiver import LocalMonitor, send_observation, send_discovered_observation, read_monitor_health
from .settings import ModeConfigError, read_mode, set_mode
from .service import MonitorUnavailable, serve_monitor
from .manual_power import assess_manual_suspend, request_manual_suspend
from .tray import run_tray, backend_probe
from .autostart import AutostartError, inspect_autostart, modify_autostart
from .keepawake import KeepAwakeError, KeepAwakeManager, PRESETS_MINUTES
from .native_controls import (
    NativeControlError, PROFILES, power_profiles, set_profile,
    display_support, display_off, open_power_settings,
)
from .native_sleep import assess_native_sleep, request_terminal_sleep
from .native_observers import read_native_activity
from .user_service import (
    UserServiceError, inspect_user_service, modify_user_service,
)
from .supervisor import monitor_service_loop


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
    print("Implementation: foundation (no automatic suspend)")
    print("ISC automatic suspend: NOT IMPLEMENTED")
    print("ISC user-service supervision: OPTIONAL (never enabled automatically)")
    try:
        current = read_mode()
        print(f"ISC operator preference: {current.mode.upper()}" +
              (" (stored)" if current.configured else " (safe default)"))
    except ModeConfigError:
        print("ISC operator preference: OFF (configuration invalid; fail-safe)")
    print("ISC ON automatic suspension: NOT IMPLEMENTED")
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
    health = read_monitor_health()
    if health is None:
        print("ISC live receiver health: unavailable")
    else:
        print(f"ISC live receiver events: {health.accepted} accepted, {health.rejected} rejected")
        print(
            f"Observed activity: active={health.observed_active}, "
            f"waiting-human={health.observed_waiting_human}, "
            f"unknown={health.observed_unknown}"
        )
        if health.claude_code_observations is not None:
            print(
                "Claimed source events (excluding synthetic probes): "
                f"Claude Code={health.claude_code_observations}, "
                f"Codex={health.codex_observations}"
            )
            print(
                "Synthetic diagnostic events: "
                f"{health.synthetic_probe_events}"
            )
            print("Source names are sender-claimed, not authenticated application identity.")
        else:
            print("Per-source observations: unavailable (receiver requires restart).")
        print("Coverage verified: NO (observations are not authority)")
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
            print("Accepted event types (no session IDs):")
            if not listener.event_counts:
                print("  none")
            for (source, event), count in sorted(listener.event_counts.items()):
                print(f"  {source}/{event}: {count}")
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


def hook_self_test(source: str) -> int:
    """Exercise synthetic hook delivery; does not invoke an AI application."""
    result = check_hook_delivery(source)
    print("ISC SYNTHETIC HOOK DELIVERY TEST")
    print("Source: " + source)
    print("Outcome: " + result.status)
    if result.before_accepted is not None:
        print("Receiver accepted before: " + str(result.before_accepted))
    if result.after_accepted is not None:
        print("Receiver accepted after: " + str(result.after_accepted))
    print("No real agent launched. No hook settings changed.")
    print("Synthetic test events never establish actual workload activity.")
    print("Updated receivers count synthetic probes separately from source activity.")
    print("Other concurrent events may affect the aggregate counter.")
    print("This test does not verify third-party hook invocation or full work coverage.")
    return 0 if result.delivered else 2


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


def hook_change(*, source: str, operation: str, confirm: bool) -> int:
    """Explicitly requested single-source hook update; never runs an agent."""
    if not confirm:
        print("No changes made. Use --confirm to authorize this operation.")
        return 2
    target = dict(default_hook_targets())[source]
    try:
        outcome = modify_existing_hooks(source, target, operation)
    except (HookInstallError, ValueError):
        print("ISC hook operation refused: configuration unsafe or changed.")
        print("Existing hooks were not intentionally replaced.")
        return 2
    if outcome.changed:
        print(f"ISC {operation}: {outcome.affected_event_groups} event group(s)")
        print(f"Private recovery backup: {outcome.backup}")
        print("No coding agent was executed.")
    else:
        print("No change required; existing hook registration already matches.")
    print("ISC automatic suspend: NOT IMPLEMENTED")
    return 0


def mode_control(selected: str | None) -> int:
    """Persist only an operator preference; not a service or power setting."""
    try:
        state = read_mode() if selected is None else set_mode(selected)
    except ModeConfigError as error:
        if str(error) == "on_not_implemented":
            print("ISC ON unavailable: automatic suspend is not implemented.")
        else:
            print("ISC mode unavailable: unsafe or invalid local configuration.")
        print("ISC automatic suspend: NOT IMPLEMENTED")
        return 2
    print(f"ISC persistent preference: {state.mode.upper()}")
    print("Stored on disk." if state.configured else "Safe default; no file written.")
    print("No background service was started or stopped.")
    print("No system power settings were changed.")
    print("ISC automatic suspend: NOT IMPLEMENTED")
    return 0


def controlled_monitor(*, seconds: float) -> int:
    """Operator-managed, foreground only; never enabled automatically."""
    try:
        summary = serve_monitor(max_seconds=seconds)
    except (MonitorUnavailable, ValueError) as exc:
        print(f"ISC monitoring refused or stopped: {str(exc)}")
        print("ISC automatic suspend: NOT IMPLEMENTED")
        return 2
    print(f"Monitor stopped: {summary.reason}")
    print(f"Accepted metadata events: {summary.accepted}")
    print(f"Rejected metadata events: {summary.rejected}")
    print("Accepted event types (no session IDs):")
    if not summary.event_counts:
        print("  none")
    for source, event, count in summary.event_counts:
        print(f"  {source}/{event}: {count}")
    print("Full workload coverage: NOT VERIFIED")
    print("ISC automatic suspend: NOT IMPLEMENTED")
    return 0


def manual_power(*, confirm: bool, action: str | None) -> int:
    """Preview by default; only explicit interactive suspend can change power."""
    if action is None:
        print("ISC MANUAL POWER")
        print("Sleep / Suspend: available as the same suspend-to-RAM action")
        print("Hibernate / Hybrid Sleep: native capability-checked manual actions")
        print("Manual suspend works even when ISC automatic mode is OFF.")
        print("Use 'isc suspend' for a read-only assessment.")
        print("ISC automatic suspend: NOT IMPLEMENTED")
        return 0
    assessment = assess_manual_suspend()
    print("ISC MANUAL SUSPEND / SLEEP")
    print(f"System capability: {assessment.capability}")
    print(f"Known candidate processes (activity UNKNOWN): {assessment.candidate_processes}")
    print("Full workload coverage: NOT VERIFIED")
    if assessment.blockers:
        print("Request blocked:")
        for code in assessment.blockers:
            print(f"  - {code}")
        print("No suspend request made.")
        return 2
    print("OS preflight: no verified blockers; this is NOT a work-safety guarantee.")
    if not confirm:
        print("PREVIEW ONLY. No suspend request made.")
        print("To suspend manually, explicitly use --confirm in a local terminal.")
        return 0
    outcome = request_manual_suspend(confirmed=True)
    if not outcome.submitted:
        print(f"No suspend request made: {outcome.reason}")
        return 2
    print("Manual suspend request submitted to systemd (resume not verified).")
    print("ISC automatic suspend: NOT IMPLEMENTED")
    return 0


def tray_autostart(*, operation: str | None, confirm: bool) -> int:
    """Check, enable, or disable ISC user autostart; never start a process."""
    print("ISC TRAY LOGIN AUTOSTART")
    try:
        if operation is None:
            status = inspect_autostart()
            print(f"Status: {status.state.upper()}")
            print("No files modified or processes started.")
            return 0 if status.state != "unmanaged" else 2
        if not confirm:
            print("No changes made. Explicit --confirm is required.")
            return 2
        outcome = modify_autostart(operation)
    except AutostartError as exc:
        print(f"Operation refused: {str(exc)}")
        print("Unrelated desktop startup entries were not intentionally changed.")
        return 2
    print(f"Status: {outcome.state.upper()}")
    print("Startup entry updated." if outcome.changed else "Already in requested state.")
    print("This does not start or stop the current tray session.")
    print("No GNOME power settings or system services changed.")
    return 0


def keep_awake_cli(minutes: int | None, forever: bool = False) -> int:
    """Foreground timed inhibitor: Ctrl+C releases, no background daemon."""
    if minutes is None and not forever:
        print("ISC KEEP AWAKE")
        print("Available durations (minutes): " + ", ".join(map(str, PRESETS_MINUTES)))
        print("Use: isc keep-awake --minutes 15, or --forever")
        print("The terminal stays occupied; Ctrl+C cancels the inhibitor.")
        print("No Keep Awake operation started.")
        return 0
    guard = KeepAwakeManager()
    try:
        state = guard.start(None if forever else minutes)
        if not state.requested:
            print("Keep Awake refused: systemd inhibitor not verified.")
            return 2
        duration = "until cancelled (no timer)" if forever else f"up to {minutes} minutes"
        print(f"Keep Awake verified: {duration}.")
        print("Automatic idle sleep and suspend are temporarily inhibited.")
        print("This terminal must remain open. Press Ctrl+C to cancel.")
        try:
            while guard.status().requested:
                time.sleep(0.5)
        except KeyboardInterrupt:
            print("Keep Awake cancelled by operator.")
        print("Keep Awake period ended or was cancelled.")
        return 0
    except KeepAwakeError as exc:
        print("Keep Awake unavailable: " + str(exc))
        return 2
    finally:
        try:
            guard.close()
        except KeepAwakeError:
            print("WARNING: could not verify inhibitor cancellation.")


def native_sleep_cli(action: str, confirmed: bool) -> int:
    assessment = assess_native_sleep(action)
    print(f"ISC NATIVE {action.upper()} PREVIEW")
    print(f"Logind capability: {assessment.capability}")
    print(f"Known candidate processes (activity UNKNOWN): {assessment.candidate_processes}")
    print("Full workload coverage and hardware resume: NOT VERIFIED")
    if assessment.blockers:
        print("Request blocked: " + ", ".join(assessment.blockers))
        return 2
    if not confirmed:
        print("PREVIEW ONLY. Use --confirm and a typed phrase for a manual action.")
        return 0
    outcome = request_terminal_sleep(action, confirmed=True)
    print(
        "Native OS request accepted (resume unverified)."
        if outcome.submitted else "No power request made: " + outcome.reason
    )
    return 0 if outcome.submitted else 2


def profile_cli(name: str | None) -> int:
    try:
        info = set_profile(name) if name is not None else power_profiles()
    except NativeControlError as exc:
        print("Native power profiles unavailable: " + str(exc))
        return 2
    print("OS power profile: " + info.current)
    print("Available on this host: " + ", ".join(info.available))
    return 0


def display_off_cli(*, confirmed: bool) -> int:
    supported, reason = display_support()
    print("Display-only power: " + reason)
    if not supported:
        print("No change made; X11 DPMS is not available in this session.")
        return 2
    if not confirmed:
        print("PREVIEW ONLY. Use display-off --confirm to turn off the image.")
        print("The computer continues working; keyboard or mouse activity can wake the display.")
        return 0
    try:
        display_off(confirm=True)
    except NativeControlError as exc:
        print("Display power action refused: " + str(exc))
        return 2
    print("Display-off request sent; system remains running.")
    return 0


def power_settings_cli() -> int:
    try:
        open_power_settings()
    except NativeControlError as exc:
        print("GNOME Power settings unavailable: " + str(exc))
        return 2
    print("Opened the operating system's Power settings.")
    return 0


def monitor_service_control(operation: str | None, confirmed: bool) -> int:
    """Optional native systemd user-service management; no power action."""
    try:
        if operation is None:
            info = inspect_user_service()
            print("ISC MONITOR USER SERVICE")
            print("Installed: " + ("YES" if info.installed else "NO"))
            print("Enabled: " + str(info.enabled))
            print("Active: " + str(info.active))
            print("MONITOR service never requests automatic suspension.")
            return 0
        if not confirmed:
            print("No changes made. Explicit --confirm is required.")
            return 2
        result = modify_user_service(operation)
        print("ISC monitor service " + operation + " requested.")
        print("Installed: " + str(result.installed))
        print("Enabled: " + str(result.enabled))
        print("Active: " + str(result.active))
        print("Automatic suspension remains disabled.")
        return 0
    except UserServiceError as exc:
        print("ISC user service operation refused: " + str(exc))
        return 2



def native_activity_cli() -> int:
    """Read-only snapshot of partial native scheduled-work/session evidence."""
    data = read_native_activity()
    print("ISC NATIVE WORK DISCOVERY (READ ONLY)")
    def shown(value):
        return "UNKNOWN" if value is None else str(value)
    print("Linux login sessions observed: " + shown(data.sessions_observed))
    print("Loaded systemd system timers: " + shown(data.loaded_system_timers))
    print("Loaded systemd user timers: " + shown(data.loaded_user_timers))
    print("Coverage: INCOMPLETE — loaded timers exclude cron, at, app schedulers,")
    print("independent services, remote work and future timer activations.")
    print("No automatic suspension permitted from this discovery.")
    print("No system settings changed.")
    return 0



def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="smart-sleep",
        description="Read-only foundation for future intelligent Linux sleep management",
    )
    parser.add_argument(
        "command", nargs="?",
        choices=("status", "idle", "inhibitors", "processes", "check", "hook-probe", "monitor", "send-hook", "hook-plan", "hook-enable", "hook-disable", "mode", "serve", "power", "sleep", "suspend", "tray", "tray-check", "autostart", "keep-awake", "hibernate", "hybrid-sleep", "power-profiles", "display-off", "power-settings", "service", "monitor-service", "native-activity", "hook-self-test"),
        default="status",
    )
    parser.add_argument(
        "--source", choices=sorted(SUPPORTED_SOURCES),
        help="Source application for the read-only hook-probe command",
    )
    parser.add_argument("--seconds", type=float, default=1.0, help="Foreground monitor seconds; serve accepts 0 for indefinite")
    parser.add_argument("--self-test", action="store_true", help="Send one synthetic Stop event to the private monitor")
    parser.add_argument("--advertise", action="store_true", help="Expose temporary receiver via a fixed private runtime socket; never changes agent hooks")
    parser.add_argument("--socket-path", help="Explicit local Unix socket path for send-hook")
    parser.add_argument("--confirm", action="store_true", help="Explicitly authorize a selected change or manual suspend")
    parser.add_argument("--startup", choices=("enable", "disable"), help="User login tray startup control; requires autostart command")
    parser.add_argument("--service", choices=("enable", "disable"), help="Manage ISC-owned systemd user monitor service; requires service command")
    parser.add_argument("--minutes", type=int, choices=PRESETS_MINUTES, help="Keep Awake duration in minutes")
    parser.add_argument("--forever", action="store_true", help="Keep Awake until cancelled while this terminal is running")
    parser.add_argument("--profile", choices=PROFILES, help="Select an OS-supported power profile")
    parser.add_argument("--set", choices=("off", "monitor", "on"), help="Persist ISC operator preference (ON currently unavailable)")
    args = parser.parse_args(argv)
    if args.command == "native-activity":
        return native_activity_cli()
    if args.command == "monitor-service":
        if args.service is not None:
            parser.error("--service is only valid with service")
        return monitor_service_loop()
    if args.command == "service":
        if args.startup is not None:
            parser.error("--startup is only valid for autostart")
        return monitor_service_control(args.service, args.confirm)
    if args.service is not None:
        parser.error("--service is only valid for service")
    if args.command == "keep-awake":
        if args.forever and args.minutes is not None:
            parser.error("choose either --forever or --minutes")
        return keep_awake_cli(args.minutes, forever=args.forever)
    if args.minutes is not None or args.forever:
        parser.error("--minutes/--forever are only valid for keep-awake")
    if args.command == "power-profiles":
        return profile_cli(args.profile)
    if args.profile is not None:
        parser.error("--profile is only valid for power-profiles")
    if args.command == "display-off":
        return display_off_cli(confirmed=args.confirm)
    if args.command == "power-settings":
        return power_settings_cli()
    if args.command in ("hibernate", "hybrid-sleep"):
        return native_sleep_cli(args.command, confirmed=args.confirm)
    if args.command == "autostart":
        if args.set is not None:
            parser.error("--set is only valid for mode")
        return tray_autostart(operation=args.startup, confirm=args.confirm)
    if args.startup is not None:
        parser.error("--startup is only valid for autostart")
    if args.command == "tray":
        return run_tray()
    if args.command == "tray-check":
        problems = backend_probe()
        if problems:
            print("ISC TRAY DEPENDENCY CHECK")
            for issue in problems:
                print("  - " + issue)
            print("No software or desktop settings changed.")
            return 2
        print("ISC tray Python dependencies: AVAILABLE")
        print("GNOME Shell AppIndicator extension status: NOT VERIFIED")
        print("No software or desktop settings changed.")
        return 0
    if args.command in ("power", "sleep", "suspend"):
        return manual_power(
            confirm=args.confirm,
            action=None if args.command == "power" else "suspend",
        )
    if args.command == "mode":
        return mode_control(args.set)
    if args.command == "serve":
        return controlled_monitor(seconds=args.seconds)
    if args.set is not None:
        parser.error("--set is only valid for the mode command")
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
    if args.command == "hook-self-test":
        if args.source is None:
            parser.error("hook-self-test requires --source")
        return hook_self_test(args.source)
    if args.command == "hook-probe":
        if args.source is None:
            parser.error("hook-probe requires --source")
        return hook_probe(args.source)
    if args.command == "monitor":
        return monitor(seconds=args.seconds, self_test=args.self_test, advertise=args.advertise)
    if args.command == "hook-plan":
        return hook_plan()
    if args.command in ("hook-enable", "hook-disable"):
        if args.source is None:
            parser.error("select --source claude_code or --source codex")
        return hook_change(
            source=args.source,
            operation="enable" if args.command == "hook-enable" else "disable",
            confirm=args.confirm,
        )
    if args.command == "send-hook":
        if args.source is None:
            parser.error("send-hook requires --source")
        return send_hook(source=args.source, socket_path=args.socket_path)
    return 2
