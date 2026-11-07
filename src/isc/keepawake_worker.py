"""Lifetime-bounded payload for a native systemd-inhibit lock.

systemd-inhibit holds its existing logind lock while this worker runs.
The worker monitors a private stdin pipe inherited from the ISC owner.
An EOF means the owner exited or crashed, so the lock is released without
requiring a surviving supervisor to know a PID. No network or system writes.
"""
from __future__ import annotations

import os
import select
import sys
import time


def run(seconds: int | None) -> int:
    if seconds is not None and not 1 <= seconds <= 14400:
        return 2

    deadline = None if seconds is None else time.monotonic() + seconds
    try:
        poller = select.poll()
        poller.register(0, select.POLLIN | select.POLLHUP | select.POLLERR)
        while True:
            if deadline is None:
                wait_ms = 500
            else:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return 0
                wait_ms = max(1, min(500, int(remaining * 1000)))
            events = poller.poll(wait_ms)
            if events:
                # Parent never sends data; only pipe closure is expected.
                # Treat any input, HUP, or error as a cancellation request.
                return 0
    except (OSError, ValueError):
        # Fail closed for the lock: release if the owner cannot be watched.
        return 2


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        return 2
    if args[0] == "forever":
        return run(None)
    if not args[0].isascii() or not args[0].isdecimal():
        return 2
    return run(int(args[0]))


if __name__ == "__main__":
    raise SystemExit(main())
