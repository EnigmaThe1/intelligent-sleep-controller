"""ISC CLI. Only an explicit, interactive manual command can request suspend."""

from __future__ import annotations

import argparse
import math
import sys
import subprocess
import time
from typing import Sequence

from . import __version__
from .gnome import read_gnome_idle_ms
from .gnome_inhibitors import read_gnome_inhibit_flags
from .hook_payload import read_hook_payload, SUPPORTED_SOURCES
from .hook_diagnostics import check_hook_delivery
from .hook_watch import watch_hook_observations
from .hook_wiring_diagnostics import inspect_hook_wiring
from .agent_hook_diagnosis import diagnose_agent_hooks
from .agent_integration_readiness import inspect_agent_integration
from .hook_plan import build_hook_plan, default_hook_targets, UnsafeHookConfiguration
from .hook_install import HookInstallError, modify_existing_hooks
from .inhibitors import read_inhibitors
from .policy import Snapshot, WorkItem, WorkState, evaluate
from .host_safety import compose_host_snapshot
from .processes import read_process_candidates
from .process_lifecycle import read_process_lineage, compare_process_lineage
from .receiver import LocalMonitor, send_observation, send_discovered_observation, read_monitor_health
from .settings import ModeConfigError, read_mode, set_mode
from .service import MonitorUnavailable, serve_monitor
from .manual_power import assess_manual_suspend, request_manual_suspend
from .tray import run_tray, backend_probe
from .autostart import AutostartError, inspect_autostart, modify_autostart
from .desktop_launcher import (
    DesktopLauncherError, inspect_launcher, modify_launcher,
)
from .desktop_window import run_window
from .keepawake import KeepAwakeError, KeepAwakeManager, PRESETS_MINUTES
from .native_controls import (
    NativeControlError, PROFILES, power_profiles, set_profile,
    display_support, display_off, open_power_settings,
)
from .native_sleep import assess_native_sleep, request_terminal_sleep
from .native_observers import read_native_activity
from .session_census import read_session_census
from .service_invocations import read_service_invocations, compare_service_invocations
from .scheduled_work import read_scheduled_work, read_timer_unit_files, read_timer_associations
from .independent_service_discovery import read_independent_services
from .timer_service_states import read_timer_service_states
from .timer_service_processes import read_timer_service_process_indicators
from .timer_service_tasks import read_timer_service_tasks
from .timer_task_watch import compare_task_counts
from .timer_service_lifecycle import (
    read_service_task_snapshots, compare_service_task_snapshots,
)
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
        if health.process_sample_age_seconds is None:
            print("Background process census: UNKNOWN (no completed sample)")
        elif health.process_roots is None:
            print(
                "Background process census: UNKNOWN "
                f"(sample {health.process_sample_age_seconds}s ago)"
            )
        else:
            print(
                "Background process census (partial): "
                f"roots={health.process_roots}, "
                f"descendants={health.process_descendants}, "
                f"visibility-races={health.process_visibility_races}, "
                f"identity-changes={health.process_identity_changes}; "
                f"sample age={health.process_sample_age_seconds}s"
            )
            if health.process_cpu_progressing is None:
                print("Observed process CPU progress: UNKNOWN (insufficient samples)")
            else:
                print(
                    "Observed process CPU progress (partial): "
                    f"progressing={health.process_cpu_progressing}, "
                    f"unchanged={health.process_cpu_unchanged}, "
                    f"changed/missing={health.process_cpu_changed_or_missing}"
                )
            print(
                "CPU inactivity does NOT prove job completion, waiting "
                "safety or sleep eligibility."
            )
            print("Background census is not proof of job completion or sleep safety.")
        if health.scheduler_sample_age_seconds is None:
            print("Background scheduler census: UNKNOWN (no completed sample)")
        elif health.scheduler_services is None:
            print(
                "Background scheduler census: UNKNOWN "
                f"(sample {health.scheduler_sample_age_seconds}s ago)"
            )
        else:
            print(
                "Background scheduler census (partial): "
                f"timer-linked services={health.scheduler_services}, "
                f"reporting tasks={health.scheduler_with_tasks}, "
                f"uncertain={health.scheduler_uncertain}, "
                f"observed changes={health.scheduler_transitions}, "
                f"uncertain transitions={health.scheduler_uncertain_transitions}, "
                f"service activation changes={health.scheduler_invocation_changes}; "
                f"sample age={health.scheduler_sample_age_seconds}s"
            )
            print("Scheduler observations do not prove lifecycle completion.")
        if health.independent_sample_age_seconds is None:
            print("Background independent services: UNKNOWN (no completed sample)")
        elif health.independent_services is None:
            print(
                "Background independent services: UNKNOWN "
                f"(sample {health.independent_sample_age_seconds}s ago)"
            )
        else:
            print(
                "Background independent services (partial): "
                f"active={health.independent_services}, "
                f"timer-linked={health.independent_timer_linked_active}, "
                f"other={health.independent_other_active}, "
                f"net count deltas={health.independent_net_count_delta}; "
                f"sample age={health.independent_sample_age_seconds}s"
            )
            print("Net service-count changes cannot establish task transitions.")
        print("Coverage verified: NO (observations are not authority)")
    print("No system configuration has been changed.")
    return 0


def idle() -> int:
    print("GNOME user idle: " + _idle_text())
    print("User idle time is NOT proof that background work has stopped.")
    print("ISC automatic suspend: NOT IMPLEMENTED")
    return 0


def gnome_inhibitors_cli() -> int:
    """Observe only the current GNOME session's idle/suspend flags."""
    flags = read_gnome_inhibit_flags()
    print("ISC GNOME SESSION INHIBITOR FLAGS — READ ONLY")
    for name, value in (
        ("Suspend inhibited", flags.suspend_inhibited),
        ("Session idle inhibited", flags.idle_inhibited),
    ):
        print(f"{name}: {'UNKNOWN' if value is None else ('YES' if value else 'NO')}")
    print("Only this GNOME session is queried; multi-session coverage is unknown.")
    print("A 'NO' is not proof of complete system inhibitor coverage.")
    print("Coverage: INCOMPLETE. Automatic suspension remains disabled.")
    print("No inhibitors or system power settings changed.")
    return 0 if (flags.suspend_inhibited is not None
                 and flags.idle_inhibited is not None) else 2


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
        print(f"Undecodable process names (partial visibility): {scan.undecodable_names}")
        for item in scan.candidates[:40]:
            print(f"  pid={item.pid} name={item.name} state=UNKNOWN")
        if scan.count > 40:
            print(f"  ... {scan.count - 40} more candidates not shown")
        print("This inventory is illustrative, not exhaustive.")
        print("A process name does NOT prove task activity or completion.")
    print("ISC automatic suspend: NOT IMPLEMENTED")
    print("No system configuration has been changed.")
    return 0


def process_watch_cli(*, seconds: float) -> int:
    """Compare two bounded /proc snapshots without making activity claims."""
    if not math.isfinite(seconds) or not (0 <= seconds <= 60):
        print("Process observation interval must be between 0 and 60 seconds.")
        return 2
    first = read_process_lineage()
    if first is None:
        print("ISC process lineage: UNKNOWN (first snapshot failed).")
        print("No sleep eligibility established.")
        return 2
    if seconds:
        time.sleep(seconds)
    second = read_process_lineage()
    if second is None:
        print("ISC process lineage: UNKNOWN (second snapshot failed).")
        print("No sleep eligibility established.")
        return 2
    change = compare_process_lineage(first, second)
    if change is None:
        print("ISC process lineage: UNKNOWN (comparison failed).")
        return 2
    print("ISC PROCESS LIFECYCLE — READ ONLY")
    print(
        f"First sample: {len(first.roots)} selected roots, "
        f"{len(first.descendants)} same-user descendants"
    )
    print(
        f"Second sample: {len(second.roots)} selected roots, "
        f"{len(second.descendants)} same-user descendants"
    )
    print(
        f"Identity continuing: {change.continuing}; "
        f"appeared: {change.appeared}; "
        f"disappeared: {change.disappeared}; "
        f"PID reused: {change.reused_pids}"
    )
    print(f"Process visibility races: {change.visibility_races}")
    print("A disappearing process does NOT prove its work completed.")
    print("Inventory is partial: different users, kernel work, timers, and")
    print("independent workloads may be unobserved.")
    print("Coverage verified: NO. Automatic suspension remains disabled.")
    print("No system or application settings changed.")
    return 0


def check() -> int:
    """Explain a conservative real-host readiness check without permitting sleep."""
    idle_ms = read_gnome_idle_ms()
    locks = read_inhibitors()
    gnome_flags = read_gnome_inhibit_flags()
    session_census = read_session_census()
    scan = read_process_candidates()
    # Share the identical conservative source composition with the
    # disarmed auto-suspend executor. Source availability is not authority.
    result = evaluate(compose_host_snapshot(
        idle_ms=idle_ms, inhibitors=locks, gnome_flags=gnome_flags,
        processes=scan, sessions=session_census,
    ))
    print("ISC READ-ONLY SAFETY CHECK")
    print(f"GNOME idle: {_format_idle_ms(idle_ms)}")
    print("Idle threshold: 1800 seconds")
    if scan is None:
        print("Process candidate inventory: UNKNOWN")
    else:
        print(f"Process candidates requiring lifecycle verification: {scan.count}")
    print("Application workload coverage: NOT VERIFIED")
    print("Scheduled work and wake safety: NOT VERIFIED")
    def _flag(value: bool | None) -> str:
        return "UNKNOWN" if value is None else ("YES" if value else "NO")

    print(
        "GNOME session inhibitors (current session): "
        f"suspend={_flag(gnome_flags.suspend_inhibited)}, "
        f"idle={_flag(gnome_flags.idle_inhibited)}"
    )
    if session_census is None:
        print("Linux login session census: UNKNOWN")
    else:
        print(
            f"Linux login sessions: {session_census.listed}; "
            f"active={session_census.active}, "
            f"inactive={session_census.inactive}, "
            f"unknown={session_census.unknown}"
        )
    print("GNOME multi-session inhibitor coverage: NOT VERIFIED")
    print("A single active login session does NOT establish safe inactivity.")
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


def hook_watch_cli(*, source: str, seconds: float) -> int:
    """Observe non-synthetic sender claims without invoking any agent."""
    print("ISC REAL HOOK OBSERVATION WINDOW — READ ONLY")
    print(f"Source: {source}")
    try:
        result = watch_hook_observations(source, seconds=seconds)
    except ValueError:
        print("Invalid observation request. No changes made.")
        return 2
    print(f"Outcome: {result.outcome}")
    print(
        "Sender-claimed events during window: "
        + ("UNKNOWN" if result.claimed_events is None
           else str(result.claimed_events))
    )
    print("No agents or hook configurations changed.")
    print("Synthetic ISC probes do not increment source observation counts.")
    print("Positive observations do NOT authenticate application identity or task ownership.")
    print("No event establishes completion or permission to suspend.")
    print("ISC automatic suspend: NOT IMPLEMENTED")
    return 0 if result.claimed_events is not None else 2


def agent_diagnose_cli(*, source: str) -> int:
    """Classify static hook readiness separately from actual claimed traffic."""
    record = diagnose_agent_hooks(source)
    print("ISC AGENT HOOK DIAGNOSIS — READ ONLY")
    print(f"Source: {record.source}")
    print(f"Outcome: {record.outcome}")
    print("Configured ISC hook event types: " + (
        "UNKNOWN" if record.configured_events is None
        else str(record.configured_events)
    ))
    print("Observed non-synthetic event claims since receiver start: " + (
        "UNKNOWN" if record.observed_events is None
        else str(record.observed_events)
    ))
    print("Receiver: " + ("AVAILABLE" if record.receiver_available
                          else "UNAVAILABLE"))
    print("Zero events can be normal when an agent is idle or not running.")
    print("A claimed event does NOT authenticate its application or task.")
    print("No agents launched or configuration and power settings changed.")
    print("ISC automatic suspend: NOT IMPLEMENTED")
    return 0


def hook_wiring_cli(*, source: str) -> int:
    """Diagnose configured ISC commands without executing or printing them."""
    result = inspect_hook_wiring(source)
    print("ISC CODING-AGENT HOOK WIRING — READ ONLY")
    print(f"Source: {result.source}")
    print(f"Configuration: {result.state}")
    print(f"Configured ISC event types: {result.installed_events}")
    print(f"Missing ISC event types: {result.missing_events}")
    print(f"Conditionally matched event types: {result.conditional_events}")
    print(f"Event types with missing local dependencies: {result.missing_dependencies}")
    print(f"Event types with duplicate ISC handlers: {result.duplicate_events}")
    print("Local command paths are checked, not executed.")
    print("Configuration presence does not prove real application delivery.")
    print("No private commands, paths or configuration contents displayed.")
    print("No hooks, agents, services or power settings changed.")
    print("ISC automatic suspend: NOT IMPLEMENTED")
    return 0 if result.state != "CONFIG_UNAVAILABLE" else 2


def agent_integration() -> int:
    """Read-only real receiver and optional agent-hook readiness summary."""
    print("ISC CODING-AGENT INTEGRATION — READ ONLY")
    for row in inspect_agent_integration():
        print(f"{row.source}:")
        print(f"  ISC hook configuration: {row.config_state}")
        if row.configured_hook_events is None:
            print("  configured events: UNKNOWN")
        else:
            print(f"  configured events: {row.configured_hook_events}")
        print(
            "  sender-claimed observations since monitor start: "
            + ("UNKNOWN" if row.observed_events is None
               else str(row.observed_events))
        )
        print(
            "  receiver: " + (
                "AVAILABLE" if row.receiver_active else "UNAVAILABLE"
            )
        )
    print("Counts exclude synthetic diagnostic probes.")
    print("Configured hooks do NOT prove delivery or application identity.")
    print("Accepted observations do NOT prove task ownership, completion or coverage.")
    print("No agent, hooks, processes, or power settings modified.")
    print("ISC automatic suspend: NOT IMPLEMENTED")
    return 0


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
    if assessment.undecodable_process_names:
        print(f"Process names unreadable: {assessment.undecodable_process_names}; coverage incomplete.")
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


def desktop_app_control(*, operation: str | None, confirm: bool) -> int:
    """Explicit GNOME Show Apps launcher only; never starts programs."""
    print("ISC APPLICATION MENU LAUNCHER")
    try:
        if operation is None:
            current = inspect_launcher()
            print(f"Status: {current.state.upper()}")
            print("Install using: isc app --install --confirm")
            print("The launcher opens the ISC window; tray startup is separate.")
            print("No files or services changed.")
            return 0 if current.state != "foreign" else 2
        if not confirm:
            print("No changes made. Explicit --confirm is required.")
            return 2
        result = modify_launcher(operation)
    except DesktopLauncherError as exc:
        print(f"Launcher operation refused: {exc}")
        print("No unrelated application entry intentionally modified.")
        return 2
    print(f"Status: {result.state.upper()}")
    print("Launcher updated." if result.changed else "Already in requested state.")
    print("GNOME Show Apps entry: Intelligent Sleep Controller.")
    print("The tray is NOT enabled at login by this command.")
    print("No applications launched, services restarted or power settings changed.")
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



def sessions_cli() -> int:
    """Show only bounded logind session counts, never user/session identities."""
    census = read_session_census()
    print("ISC LOGIN SESSION CENSUS — READ ONLY")
    if census is None:
        print("Session activity: UNKNOWN (list unavailable or ambiguous)")
    else:
        print(
            f"Sessions listed: {census.listed}; active: {census.active}; "
            f"inactive: {census.inactive}; unknown: {census.unknown}"
        )
    print("No per-session GNOME idle or inhibitor coverage established.")
    print("Other sessions and independent workloads must not be assumed idle.")
    print("Coverage: INCOMPLETE. Automatic suspension remains disabled.")
    print("No sessions or system configuration changed.")
    return 2 if census is None else 0


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



def service_invocations_cli(*, seconds: float) -> int:
    """Compare systemd service activation IDs without printing identities."""
    if not math.isfinite(seconds) or not (0 <= seconds <= 60):
        print("Service invocation observation interval must be 0–60 seconds.")
        return 2
    first = read_service_invocations()
    if seconds:
        time.sleep(seconds)
    second = read_service_invocations()
    print("ISC SYSTEMD SERVICE INVOCATION COMPARISON — READ ONLY")
    missing = False
    for label, before, after in (
        ("System", first[0], second[0]),
        ("User", first[1], second[1]),
    ):
        delta = compare_service_invocations(before, after)
        if delta is None:
            missing = True
            print(f"{label}: UNKNOWN (service invocation evidence unavailable)")
        else:
            print(
                f"{label}: retained invocation={delta.retained}; "
                f"changed invocation={delta.changed}; "
                f"unknown identity={delta.unknown}; "
                f"services appeared={delta.appeared}; "
                f"disappeared={delta.disappeared}"
            )
    print("Changed or missing invocation IDs do not prove job completion.")
    print("Unsampled activations, detached jobs and other schedulers remain unknown.")
    print("Coverage: INCOMPLETE. Automatic suspension remains disabled.")
    print("No services or system configuration changed.")
    return 2 if missing else 0


def timer_service_watch_cli(*, seconds: float) -> int:
    """Observe transient per-service transitions without revealing identity."""
    if not math.isfinite(seconds) or not (0 <= seconds <= 60):
        print("Service observation interval must be between 0 and 60 seconds.")
        return 2
    first = read_service_task_snapshots()
    if seconds:
        time.sleep(seconds)
    second = read_service_task_snapshots()
    print("ISC TIMER SERVICE LIFECYCLE WATCH — READ ONLY")
    unknown = False
    for label, before, after in (
        ("System", first[0], second[0]),
        ("User", first[1], second[1]),
    ):
        change = compare_service_task_snapshots(before, after)
        if change is None:
            unknown = True
            print(f"{label}: UNKNOWN (missing or inconsistent observations)")
        else:
            print(
                f"{label}: services {change.first_services} → "
                f"{change.second_services}; appeared {change.appeared}; "
                f"disappeared {change.disappeared}; "
                f"tasks present twice {change.stayed_with_tasks}; "
                f"gained reported tasks {change.gained_tasks}; "
                f"lost reported tasks {change.lost_reported_tasks}; "
                f"unknown transitions {change.unknown_transitions}; "
                f"zero tasks twice {change.unchanged_without_tasks}"
            )
    print("Changed, absent or zero task counts do not prove job completion.")
    print("Detached work, unsampled intervals and other schedulers are unverified.")
    print("Coverage: INCOMPLETE. Automatic suspension remains disabled.")
    print("No system settings, timers or services changed.")
    return 2 if unknown else 0


def timer_task_watch_cli(*, seconds: float) -> int:
    """Compare two partial task-count snapshots without inferring completion."""
    if not math.isfinite(seconds) or not (0 <= seconds <= 60):
        print("Timer task observation interval must be between 0 and 60 seconds.")
        return 2
    before = read_timer_service_tasks()
    if seconds:
        time.sleep(seconds)
    after = read_timer_service_tasks()
    print("ISC TIMER TASK AGGREGATE WATCH — READ ONLY")
    unknown = False
    for label, previous, current in (
        ("System", before[0], after[0]),
        ("User", before[1], after[1]),
    ):
        change = compare_task_counts(previous, current)
        if change is None:
            unknown = True
            print(f"{label}: UNKNOWN (missing or inconsistent observation)")
        else:
            print(
                f"{label}: services {change.first_services} → "
                f"{change.second_services}; reporting tasks "
                f"{change.first_with_tasks} → {change.second_with_tasks}; "
                f"unknown task counts {change.first_unknown} → "
                f"{change.second_unknown}; aggregates changed: "
                f"{'YES' if change.aggregates_changed else 'NO'}"
            )
    print("Equal aggregates can conceal individual job starts and exits.")
    print("No completed-work or scheduler-coverage claim is possible.")
    print("Coverage: INCOMPLETE. Automatic suspension remains disabled.")
    print("No system configuration or services changed.")
    return 2 if unknown else 0


def timer_service_tasks_cli() -> int:
    """Display read-only cgroup task-presence counts, never safe-idle proof."""
    system, user = read_timer_service_tasks()
    print("ISC TIMER-LINKED SERVICE TASK COUNTS — READ ONLY")
    for label, result in (("System timers", system), ("User timers", user)):
        if result is None:
            print(f"{label}: UNKNOWN (incomplete systemd metadata)")
        else:
            print(
                f"{label}: {result.timers_inspected} loaded timers; "
                f"{result.distinct_services} associated services; "
                f"{result.cgroups_reported} cgroups reported; "
                f"{result.tasks_present} services reporting tasks; "
                f"{result.zero_tasks_reported} reporting zero tasks; "
                f"{result.unknown_task_counts} task counts unknown"
            )
    print("Task counts are snapshots; zero does NOT prove work completion.")
    print("Subtasks, detached jobs, other schedulers and wake safety are unverified.")
    print("Coverage: INCOMPLETE. Automatic suspension remains disabled.")
    print("No services, timers or power settings changed.")
    return 0


def timer_service_pids_cli() -> int:
    """Display aggregate systemd service process metadata, not work completion."""
    system, user = read_timer_service_process_indicators()
    print("ISC TIMER-LINKED SERVICE PROCESS INDICATORS — READ ONLY")
    for label, evidence in (("System timers", system), ("User timers", user)):
        if evidence is None:
            print(f"{label}: UNKNOWN (metadata unavailable or ambiguous)")
        else:
            print(
                f"{label}: {evidence.timers_inspected} loaded timers; "
                f"{evidence.distinct_services} services; "
                f"{evidence.main_pid_nonzero} MainPID nonzero; "
                f"{evidence.control_pid_nonzero} ControlPID nonzero; "
                f"{evidence.neither_pid_reported} neither reported"
            )
    print("Zero PIDs do not establish child work, cgroup or external job quiescence.")
    print("This is not a complete process census or evidence of safe suspend.")
    print("Coverage: INCOMPLETE. Automatic suspension remains disabled.")
    print("No services, timers or power settings changed.")
    return 0


def timer_service_states_cli() -> int:
    """Read-only service-state snapshots, never an indication of safe sleep."""
    system, user = read_timer_service_states()
    print("ISC TIMER-LINKED SERVICE STATES — READ ONLY")
    for label, data in (("System timers", system), ("User timers", user)):
        if data is None:
            print(f"{label}: UNKNOWN (incomplete or changing systemd metadata)")
        else:
            print(
                f"{label}: {data.timers_inspected} loaded timers; "
                f"{data.distinct_services} associated services; "
                f"{data.active} active, {data.transitioning} transitioning, "
                f"{data.inactive} inactive, {data.failed} failed"
            )
    print("Inactive/exited services do not prove work or child processes finished.")
    print("This snapshot excludes unloaded timers and other scheduling systems.")
    print("Coverage: INCOMPLETE. Automatic suspension remains disabled.")
    print("No timers, services or power settings changed.")
    return 0


def timer_associations_cli() -> int:
    """Summarise effective timer-to-service metadata without printing names."""
    system, user = read_timer_associations()
    print("ISC LOADED TIMER ASSOCIATIONS — READ ONLY")
    for label, evidence in (
        ("System loaded timers", system), ("User loaded timers", user),
    ):
        if evidence is None:
            print(f"{label}: UNKNOWN (query unavailable or ambiguous)")
        else:
            print(
                f"{label}: {evidence.timers_listed} inspected; "
                f"{evidence.services_resolved} service associations, "
                f"{evidence.unresolved} unresolved"
            )
    print("Unit metadata is not proof of job execution or completion.")
    print("Transient, unloaded, application and remote schedules remain unverified.")
    print("Coverage: INCOMPLETE. Automatic suspension remains disabled.")
    print("No unit state or system configuration changed.")
    return 0


def timer_files_cli() -> int:
    """Read-only installed timer definitions; never an all-schedulers claim."""
    system, user = read_timer_unit_files()
    print("ISC INSTALLED TIMER FILES — READ ONLY")
    for label, counts in (("System timer files", system), ("User timer files", user)):
        if counts is None:
            print(f"{label}: UNKNOWN (query unavailable or ambiguous)")
        else:
            print(
                f"{label}: {counts.listed} installed definitions; "
                f"{counts.enabled} enabled, {counts.disabled} disabled, "
                f"{counts.other} other"
            )
    print("Installed definitions may be unloaded, inactive, transient or unused.")
    print("Timer files do not prove when jobs start, finish or can wake the host.")
    print("Cron, at, user applications and remote schedules are not inventoried.")
    print("Coverage: INCOMPLETE. Automatic suspension remains disabled.")
    print("No system settings, services or timers changed.")
    return 0


def independent_services_cli() -> int:
    """Aggregate active systemd services irrespective of timer associations."""
    print("ISC INDEPENDENT SERVICE DISCOVERY — READ ONLY")
    for scope, label in (("--system", "System"), ("--user", "User")):
        evidence = read_independent_services(scope=scope)
        if evidence.uncertain:
            print(f"{label}: UNKNOWN (incomplete native service discovery)")
        else:
            print(
                f"{label}: active services={evidence.active_services}; "
                f"timer-linked active={evidence.timer_linked_active}; "
                f"other active={evidence.other_active}"
            )
    print("An active service is not proof of a running application task.")
    print("Non-timer services can host work; inactive units can leave detached work.")
    print("Cron, containers, transient jobs and remote schedulers remain unverified.")
    print("Coverage: INCOMPLETE. Automatic suspension remains disabled.")
    print("No services or system settings changed.")
    return 0


def scheduled_work_cli() -> int:
    """Read-only, partial systemd timer and service state census."""
    data = read_scheduled_work()
    print("ISC SCHEDULED WORK CENSUS — READ ONLY")
    for label, kind, evidence in (
        ("System timers", "timers", data.system_timers),
        ("User timers", "timers", data.user_timers),
        ("System services", "services", data.system_services),
        ("User services", "services", data.user_services),
    ):
        if evidence is None:
            print(f"{label}: UNKNOWN (query unavailable or ambiguous)")
        elif kind == "timers":
            print(
                f"{label}: {evidence.listed} listed; "
                f"{evidence.waiting_timers} waiting"
            )
        else:
            print(
                f"{label}: {evidence.listed} listed; "
                f"{evidence.running_services} running, "
                f"{evidence.activating_services} activating"
            )
    print("Timer states do not identify the jobs they activate.")
    print("Running services may be unrelated to timers; service children")
    print("can continue after the parent unit appears inactive.")
    print("Unloaded timers, cron, at, remote jobs and app schedulers are unverified.")
    print("Coverage: INCOMPLETE. Automatic suspension remains disabled.")
    print("No system settings or services changed.")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="smart-sleep",
        description="Read-only foundation for future intelligent Linux sleep management",
    )
    parser.add_argument(
        "command", nargs="?",
        choices=("status", "idle", "inhibitors", "gnome-inhibitors", "processes", "check", "hook-probe", "agent-integration", "hook-watch", "hook-wiring", "agent-diagnose", "monitor", "send-hook", "hook-plan", "hook-enable", "hook-disable", "mode", "serve", "power", "sleep", "suspend", "tray", "tray-check", "autostart", "keep-awake", "hibernate", "hybrid-sleep", "power-profiles", "display-off", "power-settings", "service", "monitor-service", "native-activity", "sessions", "scheduled-work", "independent-services", "timer-files", "timer-associations", "timer-service-states", "timer-service-pids", "timer-service-tasks", "timer-task-watch", "timer-service-watch", "service-invocations", "hook-self-test", "process-watch", "app", "window"),
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
    parser.add_argument("--install", action="store_true", help="Register ISC in GNOME Show Apps (app command only)")
    parser.add_argument("--uninstall", action="store_true", help="Remove ISC Show Apps launcher (app command only)")
    parser.add_argument("--startup", choices=("enable", "disable"), help="User login tray startup control; requires autostart command")
    parser.add_argument("--service", choices=("enable", "disable"), help="Manage ISC-owned systemd user monitor service; requires service command")
    parser.add_argument("--minutes", type=int, choices=PRESETS_MINUTES, help="Keep Awake duration in minutes")
    parser.add_argument("--forever", action="store_true", help="Keep Awake until cancelled while this terminal is running")
    parser.add_argument("--profile", choices=PROFILES, help="Select an OS-supported power profile")
    parser.add_argument("--set", choices=("off", "monitor", "on"), help="Persist ISC operator preference (ON currently unavailable)")
    args = parser.parse_args(argv)
    if args.command == "app":
        if args.install and args.uninstall:
            parser.error("choose either --install or --uninstall")
        operation = "install" if args.install else "remove" if args.uninstall else None
        return desktop_app_control(operation=operation, confirm=args.confirm)
    if args.install or args.uninstall:
        parser.error("--install/--uninstall apply only to app")
    if args.command == "window":
        return run_window()
    if args.command == "process-watch":
        return process_watch_cli(seconds=args.seconds)
    if args.command == "scheduled-work":
        return scheduled_work_cli()
    if args.command == "independent-services":
        return independent_services_cli()
    if args.command == "timer-files":
        return timer_files_cli()
    if args.command == "timer-associations":
        return timer_associations_cli()
    if args.command == "timer-service-states":
        return timer_service_states_cli()
    if args.command == "timer-service-pids":
        return timer_service_pids_cli()
    if args.command == "timer-service-tasks":
        return timer_service_tasks_cli()
    if args.command == "timer-task-watch":
        return timer_task_watch_cli(seconds=args.seconds)
    if args.command == "timer-service-watch":
        return timer_service_watch_cli(seconds=args.seconds)
    if args.command == "service-invocations":
        return service_invocations_cli(seconds=args.seconds)
    if args.command == "native-activity":
        return native_activity_cli()
    if args.command == "sessions":
        return sessions_cli()
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
    if args.command == "gnome-inhibitors":
        return gnome_inhibitors_cli()
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
    if args.command == "agent-integration":
        return agent_integration()
    if args.command == "hook-watch":
        if args.source is None:
            parser.error("hook-watch requires --source")
        return hook_watch_cli(source=args.source, seconds=args.seconds)
    if args.command == "agent-diagnose":
        if args.source is None:
            parser.error("agent-diagnose requires --source")
        return agent_diagnose_cli(source=args.source)
    if args.command == "hook-wiring":
        if args.source is None:
            parser.error("hook-wiring requires --source")
        return hook_wiring_cli(source=args.source)
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
