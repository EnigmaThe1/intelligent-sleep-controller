"""Read-only reconciliation of coding-agent wiring and observed hook traffic.

Zero events during a quiet period do NOT imply broken integration. A successful
sender claim does NOT authenticate a vendor, task owner, or completion. No
agent process is launched; no hook configuration or power settings are changed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .hook_wiring_diagnostics import HookWiringDiagnostic, inspect_hook_wiring
from .receiver import MonitorHealth, read_monitor_health


@dataclass(frozen=True)
class HookDiagnosticSummary:
    source: str
    outcome: str
    configured_events: int | None
    observed_events: int | None
    receiver_available: bool
    coverage_verified: bool = False
    task_owner_authenticated: bool = False


def diagnose_agent_hooks(
    source: str, *,
    wiring_reader: Callable[[str], HookWiringDiagnostic] = inspect_hook_wiring,
    health_reader: Callable[[], MonitorHealth | None] = read_monitor_health,
) -> HookDiagnosticSummary:
    """Classify integration evidence; never classify quiet periods as failure."""
    if source not in ("claude_code", "codex"):
        raise ValueError("unsupported_hook_source")
    if not callable(wiring_reader) or not callable(health_reader):
        raise ValueError("invalid_diagnostic_reader")
    try:
        wiring = wiring_reader(source)
    except (OSError, ValueError, RuntimeError):
        wiring = None
    if (not isinstance(wiring, HookWiringDiagnostic)
            or wiring.source != source
            or wiring.coverage_verified is not False
            or wiring.task_owner_authenticated is not False
            or type(wiring.installed_events) is not int
            or not 0 <= wiring.installed_events <= 8):
        wiring = None
    try:
        health = health_reader()
    except (OSError, ValueError, RuntimeError):
        health = None
    if (not isinstance(health, MonitorHealth)
            or health.coverage_verified is not False):
        health = None
    count = None
    if health is not None:
        observed = (
            health.claude_code_observations
            if source == "claude_code"
            else health.codex_observations
        )
        if type(observed) is int and 0 <= observed <= 2**31:
            count = observed
        else:
            health = None
    if wiring is None or wiring.state == "CONFIG_UNAVAILABLE":
        outcome = "CONFIGURATION_UNKNOWN"
    elif wiring.state == "NO_ISC_HANDLERS":
        outcome = "NO_ISC_HANDLERS"
    elif wiring.state == "INCOMPLETE_OR_CONDITIONAL":
        outcome = "INCOMPLETE_OR_CONDITIONAL"
    elif wiring.state != "CONFIGURED_PATHS_PRESENT":
        outcome = "CONFIGURATION_UNKNOWN"
    elif health is None:
        outcome = "RECEIVER_UNAVAILABLE"
    elif count == 0:
        # This is the expected outcome when the agent is not doing any work.
        outcome = "READY_NO_ACTIVITY_OBSERVED"
    else:
        outcome = "SENDER_CLAIMS_OBSERVED"
    return HookDiagnosticSummary(
        source, outcome,
        wiring.installed_events if wiring is not None else None,
        count, health is not None,
    )
