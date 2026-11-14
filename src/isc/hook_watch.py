"""Bounded, read-only observation of non-synthetic coding-agent hook traffic.

Only aggregate receiver counters are queried. No applications, hooks, services
or processes are started, changed, or inspected. Synthetic ISC self-tests are
excluded by the existing per-source counters. A positive count delta is still
a *sender claim*, not independent proof of vendor application identity or
task completion.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import time
from typing import Callable

from .hook_payload import SUPPORTED_SOURCES
from .receiver import MonitorHealth, read_monitor_health

_MAX_WATCH_SECONDS = 120.0
_MAX_COUNTER = 2**31


@dataclass(frozen=True)
class HookWatchResult:
    source: str
    outcome: str
    before: int | None
    after: int | None
    claimed_events: int | None
    coverage_verified: bool = False
    task_owner_authenticated: bool = False


def _count(health: object, source: str) -> int | None:
    if not isinstance(health, MonitorHealth) or health.coverage_verified is not False:
        return None
    raw = (
        health.claude_code_observations
        if source == "claude_code" else health.codex_observations
    )
    if type(raw) is not int or not 0 <= raw <= _MAX_COUNTER:
        return None
    return raw


def watch_hook_observations(
    source: str, *, seconds: float = 15.0,
    health_reader: Callable[[], MonitorHealth | None] = read_monitor_health,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> HookWatchResult:
    """Observe a live monitor for a bounded window without altering anything.

    Any missing health sample or counter regression invalidates the entire
    observation window: a daemon restart must not be interpreted as quiet.
    The first sample is mandatory even if the duration is zero.
    """
    if source not in SUPPORTED_SOURCES:
        raise ValueError("unsupported_hook_source")
    if (type(seconds) not in (int, float)
            or not math.isfinite(seconds)
            or not 0 <= seconds <= _MAX_WATCH_SECONDS
            or not callable(health_reader)
            or not callable(clock) or not callable(sleeper)):
        raise ValueError("invalid_hook_watch_request")
    start = clock()
    if type(start) not in (int, float) or not math.isfinite(start):
        return HookWatchResult(source, "clock_unavailable", None, None, None)
    try:
        before = _count(health_reader(), source)
    except (OSError, ValueError, RuntimeError):
        before = None
    if before is None:
        return HookWatchResult(source, "receiver_unavailable", None, None, None)
    latest = before
    while True:
        now = clock()
        if (type(now) not in (int, float) or not math.isfinite(now)
                or now < start):
            return HookWatchResult(source, "clock_unavailable", before, None, None)
        remaining = seconds - (now - start)
        if remaining <= 0:
            break
        sleeper(min(0.5, remaining))
        try:
            current = _count(health_reader(), source)
        except (OSError, ValueError, RuntimeError):
            current = None
        if current is None:
            return HookWatchResult(source, "receiver_unavailable", before, None, None)
        if current < latest:
            return HookWatchResult(source, "counter_reset", before, current, None)
        latest = current
    delta = latest - before
    return HookWatchResult(
        source,
        "sender_claimed_activity_observed" if delta else "no_activity_observed",
        before, latest, delta,
    )
