"""Read-only live hook-integration readiness for Claude Code and Codex.

Inspects only explicitly known per-user configuration paths and existing
receiver aggregate counters. It does not display filenames, hook commands,
session identifiers, event payloads, prompts or other configuration values.
A configured hook or accepted sender-claimed observation is never evidence
of a trusted application owner or completed workload.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .hook_plan import (
    EVENTS, HookPlan, UnsafeHookConfiguration,
    build_hook_plan, default_hook_targets,
)
from .receiver import MonitorHealth, read_monitor_health

MAX_OBSERVATIONS = 2**31


@dataclass(frozen=True)
class AgentIntegrationReadiness:
    source: str
    config_state: str
    configured_hook_events: int | None
    observed_events: int | None
    receiver_active: bool
    coverage_verified: bool = False
    task_owner_authenticated: bool = False


def inspect_agent_integration(
    *,
    targets: tuple[tuple[str, Path], ...] | None = None,
    plan_reader: Callable[[str, Path], HookPlan] = build_hook_plan,
    health_reader: Callable[[], MonitorHealth | None] = read_monitor_health,
) -> tuple[AgentIntegrationReadiness, ...]:
    """Inspect current live monitoring and optional coding-agent hook setup.

    UNKNOWN is distinct from zero. The only observations counted are aggregate
    sender-claimed values already collected by the MONITOR receiver; no new
    hook or application is started and no synthetic event is dispatched.
    """
    if targets is None:
        targets = default_hook_targets()
    if not callable(plan_reader) or not callable(health_reader):
        raise ValueError("invalid_agent_integration_readers")
    try:
        health = health_reader()
    except (OSError, ValueError, RuntimeError):
        health = None
    if not isinstance(health, MonitorHealth) or health.coverage_verified:
        health = None
    result: list[AgentIntegrationReadiness] = []
    for source, target in targets:
        if source not in ("claude_code", "codex"):
            raise ValueError("unsupported_agent_integration_source")
        state = "UNKNOWN"
        configured: int | None = None
        try:
            plan = plan_reader(source, target)
            if (
                not isinstance(plan, HookPlan)
                or plan.source != source
                or plan.read_only is not True
                or any(event not in EVENTS for event in plan.already_installed)
                or len(set(plan.already_installed)) != len(plan.already_installed)
            ):
                raise ValueError("invalid_agent_hook_plan")
            configured = len(plan.already_installed)
            if configured == len(EVENTS):
                state = "CONFIGURED"
            elif configured > 0:
                state = "PARTIAL"
            else:
                state = "NOT_CONFIGURED"
        except (OSError, ValueError, UnsafeHookConfiguration):
            pass
        observed: int | None = None
        if health is not None:
            count = (
                health.claude_code_observations
                if source == "claude_code"
                else health.codex_observations
            )
            if type(count) is int and 0 <= count <= MAX_OBSERVATIONS:
                observed = count
        result.append(AgentIntegrationReadiness(
            source=source,
            config_state=state,
            configured_hook_events=configured,
            observed_events=observed,
            receiver_active=health is not None,
        ))
    return tuple(result)
