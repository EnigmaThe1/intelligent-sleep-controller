# Architecture — proposed contract

ISC is standalone, offline, and Linux-first. This is a **design document**, not a claim that the daemon or integrations already exist.

## Layers

1. **Adapters** report trusted lifecycle events from independent applications; app-specific packages are optional.
2. **Work registry** now supports pure in-memory event leases with opaque task IDs, monotonic receipt times, child-work flags, and expiry -> UNKNOWN. Process/child ownership, trusted transport, persistence, and adapters remain future work. Registry emptiness after restart **cannot** establish coverage.
3. **Safety policy** takes an explicit snapshot and returns an explainable eligibility verdict. The initial pure policy is implemented in `src/isc/policy.py`.
4. **Operator controls** persist ON / OFF / MONITOR; Keep Awake is a separate expiring override.
5. **Executor** (future) coordinates GNOME idle settings, systemd/logind sleep inhibitors, timer/wake checks, final pre-suspend validation, and system suspend.
6. **Audit trail** records decisions, state transitions, and reason codes locally without storing prompts, filenames, credentials, or personal logs.

## Work states

| State | Meaning | Holds awake? |
| --- | --- | --- |
| ACTIVE | Tool, build, rendering, inference, upload, etc. in progress | Yes |
| WAITING_EXTERNAL | Network/API/dependency result pending | Yes |
| WAITING_HUMAN | Cannot proceed without a human | Not alone, after safe checks |
| COMPLETED | No outstanding operation | Not alone |
| UNKNOWN | Incomplete, expired or conflicting evidence | Yes |

A job marked WAITING_HUMAN does **not** become safe if it still has active child work. One active independent job blocks sleep even if several other jobs have completed.

## Coverage is a safety property

A process table cannot reliably distinguish an interactive assistant awaiting permission from one waiting for a network response. Low CPU/GPU use, old process age, and a Unix `S` state are **not evidence of safe idleness**. Optional adapters may use documented app hooks, supervised child-process lifecycles, and in-flight request tracking.

For automatic suspend, coverage must account for independent work not represented by adapters, with a conservative UNKNOWN fallback. On a general-purpose Linux desktop, universal workload coverage may not be possible; ISC must not silently pretend otherwise. Explicit manual opt-in and scoped supervision can make the protection guarantee meaningful.

## ON / OFF / MONITOR (future)

- **OFF**: ISC initiates no automatic suspend; explicit settings ensure ISC does not leave GNOME auto-suspend enabled unintentionally. External policy managers/manual user suspend remain outside this guarantee.
- **MONITOR**: collects safe metadata, evaluates readiness, never suspends.
- **ON**: only a trusted and current eligible verdict may reach a privileged, narrow suspend executor. Another fresh validation happens immediately before suspend.
- **KEEP AWAKE**: time-limited operator protection, independent from persistent mode.

Preferences survive reboots. A crash or missing adapter must not trigger sleep. Future GNOME/systemd changes must be transactional, reversible, and limited to the necessary settings.

## Suspend / resume

Preserve other applications' existing inhibitors. Build a well-defined wake policy for essential scheduled work; a timer without `WakeSystem` may run only after manual wake. Local workflows resume and revalidate network, GPU, and service connections after suspend. Suspend does not execute work while sleeping.

## Privilege boundaries

The user-level core is unprivileged. If privileged operations are required, expose a minimal and auditable helper with constrained inputs; do not run generic shell commands supplied by adapters. No credentials, API keys, network calls, or telemetric reporting are required.

## Current monitor-only foundation

A user can run `PYTHONPATH=src python3 -m isc check` to see current GNOME idle time and logind inhibitor classifications. It always withholds automatic suspension because independent workload coverage, GNOME session inhibitors, and scheduled-work/wake safety are **not yet verified**. The in-memory `WorkRegistry` is currently a library for tests and future trusted adapters, not a running daemon.

## Conservative lifecycle-event interpretation

`src/isc/events.py` is a pure adapter contract for future implementations. A tool-start event implies activity, but a permission request may be automatically resolved, and a Stop/turn-completed event may reflect only a turn boundary. Such events yield UNKNOWN; they do not permit sleep. Confirmed WAITING_HUMAN or COMPLETED states require a trusted adapter to verify **all** child work is quiescent.

Vendor names in the example mapping are optional and never imply any runtime dependency. There is currently **no hook installation, local event server, authenticated transport, or guarantee that all running applications have been observed**. A future receiver must validate same-user origin and process/task ownership, never trust client-provided timestamps, reject stale events, and make missed events/failures block automatic sleep.

## Optional command-hook payload parser

`src/isc/hook_payload.py` reads at most 64 KiB from an explicitly provided binary stream. It admits only the documented common `session_id` and `hook_event_name` fields, hashes the session ID and drops the rest of the payload. The probe CLI outputs no session fingerprint or raw hook input. No content from `tool_input`, `tool_response`, `last_assistant_message`, `transcript_path`, `cwd`, or environment is retained or logged.

Hook input is untrusted. A fingerprint is **not an authentication token**. A future authenticated local receiver must track event loss, source ownership, supervision of active children, and cross-session workload coverage before using any event to decide that sleep is safe. A command hook invocation must never block or modify the original application's execution.
