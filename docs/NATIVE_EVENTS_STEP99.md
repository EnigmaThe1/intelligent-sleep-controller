# Step 99 — native systemd user-manager capability probe (MONITOR only)

## Objective and implementation

The first non-synthetic Linux-native Step 99 component is a **bounded,
read-only, on-demand** D-Bus availability check implemented in
`src/isc/native_systemd_probe.py`.

The probe makes a fixed `org.freedesktop.DBus.NameHasOwner` call for
`org.freedesktop.systemd1` on the **user** bus through `busctl --user call`.
It does not call `StartServiceByName`, start a daemon, subscribe to a bus,
install anything, change permissions, or modify a power setting. It runs only
when explicitly invoked by code, and is not wired into ISC's running MONITOR,
the CLI, the tray, or the sleep eligibility policy.

It enforces a four-second deadline, a strict output format, no shell,
and a minimal environment. All errors, malformed replies, permission
failures and timeouts return UNKNOWN. Output text is never surfaced.
Only `PRESENT_UNVERIFIED`, `ABSENT_UNVERIFIED` or `UNKNOWN` is exposed,
and all authority/coverage flags remain false.

## Limitations

A user-manager name owning the bus proves **neither** signal subscription
capability **nor** event-delivery integrity. It cannot account for other
users, the system manager, disconnected readers, dropped D-Bus messages,
incomplete manager signals, independent jobs, or arbitrary development
processes. Presence and absence both remain non-authoritative.

A future actual signal subscriber requires explicit startup/snapshot ordering,
a tested narrowly scoped match rule and connection lifecycle, bounded
processing under backpressure, resilient shutdown and explicit UNKNOWN
on disconnection or loss. It must verify relevant service identities using
existing systemd InvocationID/cgroup checks, avoid retaining private message
bodies, and must never claim workload completion merely from signal silence.

## Verification

The isolated unit tests in `tests/test_native_systemd_probe.py` mock
the external call, including malformed replies and failures. No CI test
contacts the running D-Bus.

**Step 99 scope: initial native capability probe, not yet a live signal
observer.** Further adapter work remains before it can be considered complete.

ISC remains MONITOR-only. Automatic suspend remains unimplemented.
