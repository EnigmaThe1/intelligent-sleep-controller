# Step 99h — Bounded native observation-window continuity diagnostics

Step 99g was locally verified on Ubuntu (871 tests passed, active ISC MONITOR
service and a QUIET_UNVERIFIED bracketed signal probe). The repeated
observation windows are still separate read-only snapshots. A quiet or
matching window cannot certify the unsampled intervals between them.

This increment introduces a pure optional in-memory class,
`NativeWindowContinuity`, to report observation-window freshness and
loss conservatively. It accepts only previously validated aggregate
`BracketedSignalSummary` states and explicit monotonic start/end times.
It stores a bounded count (256), last observation time and the last blind
gap; it never retains raw D-Bus names, service identities, process IDs,
payloads or paths.

Any owner change, sender mismatch, observation failure, clock reversal,
invalid duration, gap exceeding 60 seconds or capacity exhaustion becomes
**sticky UNKNOWN** for that observer. Explicit source-loss notification
also becomes UNKNOWN. Brief gaps are reported but are not considered
continuous coverage; even adjacent windows do not establish event-delivery
completeness. No events are invented from quiet windows.

This is a *standalone pure model*, not yet a persistent running event reader
or a complete Step 99h workload-activation correlation. Integration requires
a trustworthy live delivery transport, manager identity consistency,
validated invocation/cgroup linkage, and explicit scheduler coverage.
No signal count proves that independent development agents have completed.

All tests are synthetic and perform no real suspend, D-Bus connection or
systemd service mutation. The ON executor remains unimplemented, and ISC
continues in MONITOR-only mode.
