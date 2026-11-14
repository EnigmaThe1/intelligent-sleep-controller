# Step 99b — optional, bounded systemd user signal watch

The first on-demand live signal observation helper is
`src/isc/native_systemd_signal_watch.py`. It is **not** wired into the
running ISC MONITOR service, safety policy, CLI, tray, or any automatic
suspend path.

The helper attempts a read-only, same-user bus watch using native `busctl`
with a match restricted to signals from the systemd Manager interface and
the well-known `org.freedesktop.systemd1` peer. It stops after at most
one matching message or an explicit 1–10 second timeout. Standard input,
output and error all use DEVNULL, so no D-Bus message bodies, unit names,
job IDs, paths or other raw output enter ISC's Python memory or logs.

A zero exit status yields `EVENT_OBSERVED_UNVERIFIED` with a count of one
only. A timeout yields `QUIET_UNVERIFIED`, with **unknown** event count.
Failed command, permission error, missing executable and unsupported
options yield UNKNOWN. No state claims that application work completed,
event delivery was exhaustive, the bus has no other work, or suspension
would be safe. A one-shot observation has no persistence, replay,
identity authentication, or disconnected-event recovery.

**Compatibility limitation:** `busctl --limit-messages=` was introduced
in systemd v257. Ubuntu installations using earlier systemd versions
may return UNKNOWN from this experimental helper. Do not imply support
from `NameHasOwner` alone. A portable monitor transport with controlled
startup, bounded output, stop/reap, and honest stream-loss reporting is
still required before a live subscriber can be enabled on the host.

No CI tests connect to D-Bus: the 11 new synthetic unit tests mock the
command, enforce matching and time bounds, and assert that neither
success nor silence grants workload coverage. No installation, power
configuration, service change or suspension is performed.

**Status:** Step 99b experimental read-only one-shot observation only;
continuous native event supervision remains outstanding.
