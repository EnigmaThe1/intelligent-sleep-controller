# Step 98 — systemd signal interruption safety model

This milestone introduces a pure, bounded MONITOR-only state model for
hypothetical native systemd manager signals. It makes **no** D-Bus connection,
installs **no** subscription, touches **no** power setting, and does **not**
consume real application events.

`src/isc/native_signal_stream.py` accepts only known signal-family labels:
`JobNew`, `JobRemoved`, `UnitNew`, `UnitRemoved`, and
`PropertiesChanged`. It stores no unit name, job ID, paths, arguments or
message contents. After an explicit synthetic connection, zero events means
`OBSERVING_UNVERIFIED`, **not** a safe/idle result.

A disconnect, duplicate connection attempt, invalid event, invalid or
reversed monotonic time, gap exceeding 60 seconds, or the 257th event marks
the instance sticky UNKNOWN. Even a perfectly healthy source is not
exhaustive: D-Bus signals do not guarantee coverage of other schedulers,
detached work, agent child tasks, or all work initiated between connection
establishment and subscription installation. Native signal names are only
diagnostic observations; they are not authenticated task-owner claims.

The 60-second bound is a diagnostic freshness limit, not proof of stream
continuity. A future live reader must independently demonstrate subscription
startup ordering, system/user manager permission handling, reconnect
discontinuities, multi-manager coverage, and resource-limited operation.
Do not wire it to automatic suspend or mark any coverage flag true.

Testing: `tests/test_native_signal_stream.py` exercises synthetic accepted
signals, silence, disconnects, duplicate connections, missing connections,
clock errors, malformed input, gaps, limits, and privacy boundaries.

ISC remains MONITOR-only. Automatic suspend is not implemented.
