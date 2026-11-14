# Step 99i — Opt-in live-window composition (MONITOR-only)

The local Step 99h verification passed 886 tests, and the running ISC
MONITOR service remained active with DO NOT AUTO-SUSPEND.

This increment connects the existing optional Step 99g real user-bus
bracketed observer to the Step 99h fail-closed continuity model. The new
`inspect_native_windows()` entry point runs at most four on-demand
observation windows of 1–10 seconds each. Each window reads the
systemd user's unique D-Bus owner before and after a bounded observation.
Observation times are measured with monotonic clock readings.
The existing continuity model reports blind intervals between windows.

Owner changes, unverified source results, clock regressions, malformed
observation outcomes, long gaps and observer errors abort the sequence
with UNKNOWN. The composition does not retry or conceal the loss by
starting a fresh continuity state within the same run.

The diagnostic result includes only state, number of completed windows,
number of windows containing a sender-correlated candidate, and the last
blind gap. It stores no user identifiers, D-Bus unique names, unit
identifiers, process names, paths or event payloads in the result.

**Important:** Two successful observation windows do not establish a
continuous subscription. No event backlog, reliable delivery or
application task-owner authentication has been proven. Silence and
`JobRemoved` cannot establish that application processes or detached
agents have finished. A sender-correlated event is still diagnostic only.
All authority and coverage flags remain false.

This opt-in library call is not wired into the live ISC service, CLI, tray,
system power, scheduler or automatic suspend. CI tests inject synthetic
observers and clocks and never connect to D-Bus. Future work must implement
proper subscription startup and loss detection, verify complete service/
cgroup process lifecycles, and account for independently scheduled work.

ISC remains MONITOR-only. Automatic suspend is not implemented.
