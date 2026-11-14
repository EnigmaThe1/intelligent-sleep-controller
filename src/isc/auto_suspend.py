"""ISC 1.0 automatic suspend: guarded execution path, DISARMED in 1.0 alpha.

Unlike a manual suspend request, no user confirmation or partial workload
evidence may override the decision gate. This module is not wired into a
periodic daemon, UI or CLI. As long as the shipped central mode gate does not
arm ON, its native sleep action is unreachable.

The real auto scheduler will need trusted fresh snapshot sources, a final
mode-change serialization strategy and hardware verification before ON may
become available. Never make missing evidence mean idle.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .auto_native import (
    AutoNativeAssessment, AutoNativeSubmitResult, assess_auto_native_suspend,
    submit_automatic_native,
)
from .mode_gate import EffectiveMode, current_mode
from .policy import Snapshot, evaluate
from .host_safety import read_host_snapshot


@dataclass(frozen=True)
class AutomaticSuspendResult:
    submitted: bool
    reason: str


def _armed(mode: object) -> bool:
    return (
        isinstance(mode, EffectiveMode)
        and mode.mode == "on"
        and mode.configuration_valid is True
        and mode.automatic_suspend_allowed is True
    )


def attempt_automatic_suspend(
    *,
    snapshot_reader: Callable[[], Snapshot] | None = read_host_snapshot,
    mode_reader: Callable[[], EffectiveMode] = current_mode,
    native_preflight: Callable[[], AutoNativeAssessment] = assess_auto_native_suspend,
    submit: Callable[[], AutoNativeSubmitResult] = submit_automatic_native,
) -> AutomaticSuspendResult:
    """One guarded auto request. Never schedules itself or enables ON.

    Reads two fresh host snapshots through the same source as 'isc check'. The
    native preflight confirms real systemd capability and clear sleep/idle
    inhibitors. Workload coverage is assessed ONLY by the separate trusted
    host Snapshot, never by manual-suspend consent or process heuristics. A final mode check
    is performed immediately before the native request, whose own preflight
    rechecks capability/inhibitors. OFF and MONITOR cannot call submit.

    Host source coverage is currently incomplete and cannot approve sleep.
    In this release the shipped mode gate *always* disables auto execution.
    Dependency injection is only for deterministic tests; never expose an
    external API that can inject forged trusted snapshots or ON state.
    """
    refused = AutomaticSuspendResult(False, "automatic_suspend_disarmed")
    if not callable(mode_reader):
        return refused
    try:
        first_mode = mode_reader()
    except (OSError, RuntimeError, ValueError, TypeError):
        return refused
    if not _armed(first_mode):
        return refused
    if (not callable(snapshot_reader) or not callable(native_preflight)
            or not callable(submit)):
        return AutomaticSuspendResult(False, "trusted_inputs_unavailable")

    try:
        initial = snapshot_reader()
        if not isinstance(initial, Snapshot) or not evaluate(initial).eligible:
            return AutomaticSuspendResult(False, "initial_safety_check_blocked")

        native = native_preflight()
        if (not isinstance(native, AutoNativeAssessment)
                or native.ready is not True
                or native.capability != "yes"
                or native.blockers):
            # Native readiness is not application workload coverage.
            return AutomaticSuspendResult(False, "native_safety_check_blocked")

        final = snapshot_reader()
        if not isinstance(final, Snapshot) or not evaluate(final).eligible:
            return AutomaticSuspendResult(False, "final_safety_check_blocked")

        last_mode = mode_reader()
        if not _armed(last_mode) or last_mode != first_mode:
            return AutomaticSuspendResult(False, "operator_mode_changed")

        response = submit()
    except (OSError, RuntimeError, ValueError, TypeError):
        return AutomaticSuspendResult(False, "safety_source_unavailable")

    if not isinstance(response, AutoNativeSubmitResult):
        return AutomaticSuspendResult(False, "native_request_unverified")
    if response.submitted is True:
        return AutomaticSuspendResult(True, "request_accepted_by_systemd")
    return AutomaticSuspendResult(False, "native_request_refused")
