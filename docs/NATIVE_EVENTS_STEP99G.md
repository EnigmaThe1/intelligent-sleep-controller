# Step 99g — Bracketed live Manager signal owner correlation

This increment connects the existing Step 99e bounded user-bus signal
reader to the Step 99f unique-name owner checks. It remains optional and
requires an explicit in-process call; it is **not** connected to ISC's
MONITOR service, CLI, tray, hooks, scheduler, or sleep policy.

A candidate systemd Manager header may pass its unique sender through a
private in-memory callback. The opt-in bracketed probe reads the D-Bus
owner of `org.freedesktop.systemd1` **before** starting the bounded
signal window and **after** the window. Only matching, correctly formed
owners and the captured candidate produce `SENDER_MATCH_UNVERIFIED`.
Ownership changes, sender mismatch, malformed/duplicate callbacks,
missing owners and tool failures remain non-authoritative. Raw headers,
identities, event payloads and bus messages are not printed or persisted.

The owner lookup is not atomic with D-Bus delivery. Identical before/after
owners cannot rule out temporary owner rotation or lost messages, and a
signal could occur before the monitor's match rule is installed. A sender
match is NOT authenticated application-task ownership and is NOT a signal
that the corresponding job has ended. In particular, the existing
`JobRemoved` signal must not imply successful completion of detached
processes, application work or child agents.

The existing `probe_live_user_signal()` public result remains aggregate
only. The new private callback is ignored unless explicitly requested.
No registration or suspend permission is created; all coverage and
authority flags remain false. The 10-second upper bound and 16 KiB
in-memory limit from Step 99e still apply.

## Outstanding before persistent monitoring

- Validate the actual framed busctl stream and manager-name ownership
  under live tests on the target systemd 255 machine.
- Verify subscription startup and owner changes without gaps.
- Add bounded queue/backpressure handling and persistent disconnection
  detection; fail closed on missed events and restarts.
- Correlate service invocation/cgroup membership and independently
  authenticated task ownership; cover other schedulers and active sessions.
- Keep the suspend executor unavailable until comprehensive safety,
  wake/resume and hardware testing has passed.

ISC remains MONITOR-only. Automatic suspension is not implemented.
