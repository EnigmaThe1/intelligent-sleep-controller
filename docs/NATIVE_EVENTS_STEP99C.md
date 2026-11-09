# Step 99c — systemd 255 portable bounded user-bus window

The installed Ubuntu host uses systemd 255.4; `busctl monitor` supports
`--match=` in that version, but not `--limit-messages=` (introduced in
systemd 257). The new optional `native_systemd_portable_window` module
uses the older syntax, with a hard 1–10 second subprocess timeout.

## Strict diagnostic semantics

The watch is scoped to user-bus systemd Manager interface signals. It
discards all standard output/error immediately using DEVNULL; ISC does not
store or parse message bodies, unit identifiers or paths. The monitor is
never launched by the existing service, CLI or tray and cannot change
settings. It does not require privilege escalation.

On timeout, the subprocess is killed and waited for by `subprocess.run`.
The only return value is `WINDOW_ELAPSED_UNVERIFIED`: the tool was not
observed to exit early, but there is NO proof that subscription setup
succeeded or any signal was delivered. Early exits, errors, unsupported
options or permission failure report UNKNOWN. An observed event count is
never returned, including when the process exits 0.

This experiment tests native transport feasibility on systemd 255, **not**
continuous event monitoring or task completion. An actual delivery-aware
adapter still requires a narrow authenticated source, a bounded metadata-only
protocol with startup handshake, loss/backpressure detection and independent
service/cgroup verification. No signal silence may certify idle.

All unit tests use a fake subprocess runner. A live opt-in local check
should be performed only after the target system's busctl arguments and
session privileges are confirmed. No CI test connects to D-Bus.

ISC remains MONITOR-only. Automatic suspend is NOT IMPLEMENTED.
