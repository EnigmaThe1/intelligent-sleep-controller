# Step 97 — Linux-native workload event assessment (MONITOR only)

## Current baseline

ISC already reads systemd timer associations, service invocation IDs,
TasksCurrent and cgroup membership using bounded `systemctl` queries.
Those are point-in-time observations. Two unchanged samples cannot exclude
a short-lived job that started and finished between them.

## Native mechanisms evaluated

| Facility | Potential use | Important limitation | Decision |
| --- | --- | --- | --- |
| systemd D-Bus manager signals (JobNew/JobRemoved, UnitNew/UnitRemoved) | Observe systemd manager job/loaded-unit transitions | Manager jobs do not prove an application's detached children exited. Connections can disconnect; historical activity is not recovered automatically. Both system/user managers require scoped monitoring. | Candidate for a future opt-in reader; **not** a completion source |
| systemd D-Bus PropertiesChanged and systemd InvocationID | Identify changed unit properties and service activations | Not all state changes are guaranteed by a single property signal; properties must be read/revalidated. Other process owners and schedulers remain outside scope. | Reuse existing invocation verification; assess subscription loss |
| systemd journal (journalctl / native journal API) | Inspect recorded service events with cursors and boot-scoped timestamps | Retention, access permissions, filtering, rotation/vacuum and cursor discontinuity prevent an event subset from proving exhaustive coverage. Journal cursors are **opaque** and not consecutive counters. Absence of events is not inactivity. | Step 97 pure envelope assessment only; **no live reader** |
| cgroup v2 process accounting/events | Observe current membership and some lifecycle counters | Per-cgroup state is not global; delegation, short-lived/reparented workers and reset/recreation require explicit owner verification. | Continue bounded native cgroup checks; no automatic owner claim |
| proc connector / eBPF / audit | Kernel-level process lifecycle signals | Permissions, portability, gaps, resource usage and sensitive telemetry make them unsuitable as default dependencies. | Do not introduce |
| inotify/fanotify | Filesystem changes | Not general process/task lifecycle telemetry. | Not suitable |

## Implemented in Step 97

`src/isc/native_journal_sequence.py` provides a **pure** bounded validator
for candidate journal event batches. It checks the presence and consistency
of opaque cursors, boot IDs and monotonic timestamps, rejects duplicates,
invalid formats, time reversal and boot changes, and returns aggregate-only
diagnostics. It deliberately ignores message text, unit names, arguments,
paths and other journal attributes. No journal reader, subscription, socket,
process, power control, or configuration change is introduced.

Even a valid ordered batch reports `ORDERED_UNVERIFIED`, with
`coverage_verified=False`, `uninterrupted_events_verified=False` and
`task_owner_authenticated=False`. Empty batches are `EMPTY_UNVERIFIED`
rather than idle. Invalid/oversize batches are UNKNOWN.

## Prerequisites before a live adapter

- Establish narrow opt-in source scope and actual journal/systemd permissions
  without privileged, global or sensitive event collection by default.
- Explicitly handle subscription startup, disconnects, restart, reboot,
  lost history, journal rotation and source reconnection as UNKNOWN.
- Prove owner and process-to-service correlation independently of user-space
  event sender claims; verify cgroup subtree and detached work.
- Account for independent services, cron/at, remote triggers, applications,
  and scheduled wake requirements.
- Keep all raw event details ephemeral, output only bounded aggregates,
  and never derive task completion or safe suspension from silence.
- Add synthetic tests for stream gaps, restart races, evictions and privilege
  failures before any live MONITOR wiring.

No current event source may authorize automatic suspend. ISC remains MONITOR-only.
