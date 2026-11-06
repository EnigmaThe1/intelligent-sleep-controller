"""Safe, synthetic end-to-end test of the existing ISC hook relay.

No agent is started, no hook configuration is read or edited, and no
power-management operation is performed. A fake, randomly generated
session is sent through the same command-line hook sender used by agents.
The receiver's aggregate counters are checked before and after. This
does not prove that a third-party application has invoked its own hook.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time

from .hook_payload import SUPPORTED_SOURCES
from .receiver import read_monitor_health


@dataclass(frozen=True)
class HookTestResult:
    source: str
    status: str
    before_accepted: int | None
    after_accepted: int | None

    @property
    def delivered(self) -> bool:
        return self.status == "receiver_counter_incremented"


def _minimal_env() -> dict[str, str]:
    environment = {
        name: os.environ[name]
        for name in (
            "HOME", "XDG_RUNTIME_DIR", "PATH", "LANG", "LC_ALL", "TZ",
        )
        if name in os.environ
    }
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return environment


def check_hook_delivery(source: str = "claude_code") -> HookTestResult:
    """Test this user's current receiver without changing its configuration.

    Only the accepted counter increment is observed; concurrent live
    activity can also increment this counter, so this alone is not proof
    that a real application has delivered its hook.
    """
    if source not in SUPPORTED_SOURCES:
        return HookTestResult(source, "unsupported_source", None, None)
    before = read_monitor_health()
    if before is None:
        return HookTestResult(source, "receiver_unavailable", None, None)

    # A STOP event is conservatively UNKNOWN; never submit a synthetic
    # completion or authorization to sleep.
    payload = json.dumps(
        {
            "session_id": "isc-synthetic-probe-" + secrets.token_hex(12),
            "hook_event_name": "Stop",
        },
        separators=(",", ":"),
    ).encode("ascii")

    try:
        sender = subprocess.run(
            [
                sys.executable, "-B", "-m", "isc",
                "send-hook", "--source", source,
            ],
            input=payload,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=_minimal_env(),
            check=False,
            timeout=4,
        )
    except (OSError, subprocess.TimeoutExpired):
        return HookTestResult(
            source, "hook_sender_unavailable", before.accepted, None,
        )
    if sender.returncode != 0:
        return HookTestResult(
            source, "hook_sender_failed", before.accepted, None,
        )

    deadline = time.monotonic() + 1.5
    after = None
    while True:
        after = read_monitor_health()
        if after is not None and after.accepted > before.accepted:
            return HookTestResult(
                source, "receiver_counter_incremented",
                before.accepted, after.accepted,
            )
        if time.monotonic() >= deadline:
            break
        time.sleep(0.05)
    return HookTestResult(
        source, "delivery_unverified", before.accepted,
        None if after is None else after.accepted,
    )
