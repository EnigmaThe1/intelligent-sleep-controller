# Step 99e — bounded native Manager signal-header probe

Ubuntu systemd 255 supports `busctl --user monitor` with `--match`.
This step adds an **opt-in, on-demand** watcher to attempt to observe one
real systemd Manager signal, with a fixed 1–10 second deadline and a
strict 16 KiB in-memory byte budget. The watcher consumes only a narrow
same-user Manager signal stream, validates the textual two-line signal
header, and returns aggregate-only status. Raw output is never logged,
persisted, returned or displayed. Standard error is discarded.

The watcher terminates and reaps its subprocess on completion. It does
not run automatically, install any hooks or subscribers, use sudo, enable
power operations or change running workloads. It is not wired into ISC's
monitor service, tray, CLI or sleep eligibility checks.

A matching header is reported as `CANDIDATE_SIGNAL_UNVERIFIED`. This is
stronger evidence than a command simply remaining open, but still cannot
authenticate the unique-name sender, demonstrate stream continuity, or
prove that any service's child jobs completed. Silence is
`QUIET_UNVERIFIED`; early exit, invalid timing or malformed input is
UNKNOWN. All coverage and authority flags remain false.

**Limitations / release gate:** Before enabling persistent supervision,
implement unique-bus-name owner verification, match installation handshake,
a loss-aware bounded transport, actual systemd metadata correlation, and
recovery from interrupted streams. Do not interpret silence or JobRemoved
as safe workload completion. The tested Python code recognises the
systemd 255 text format, which is not a guaranteed long-term wire protocol.

No tests open the real bus. Tests use synthetic header samples to reject
wrong message types, paths, interfaces, incomplete frames and spoofed
payloads. Local explicit opt-in verification can check if a genuine
matching event happens within the observation window; absence of events
is not a failure of the workstation.

ISC remains MONITOR-only. Automatic suspend is NOT IMPLEMENTED.
