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

## Foreground local event transport

The optional `LocalMonitor` creates a temporary user-owned directory (0700) under the private `XDG_RUNTIME_DIR` and a Unix stream socket (0600). It checks Linux `SO_PEERCRED` and accepts only same-UID peers. Input is capped at 1024 bytes and includes **only** a source label, event name, and fingerprinted session identifier. The receiver derives states from known events; it does not accept a client-provided `COMPLETED` or `WAITING_HUMAN` state, and expires its in-memory task leases to UNKNOWN.

Same-UID identity is not proof that an agent or process owns an event: other same-UID programs can send spoofed events. The receiver therefore **never** declares workload coverage complete. Stale/undelivered events, incomplete child-process awareness, background jobs, unrepresented sessions, and scheduled-work risks continue to prohibit automatic sleep. No source configuration is changed and no sleep executor exists.

## Opt-in socket discovery

`monitor --advertise` creates the predictable directory `$XDG_RUNTIME_DIR/isc-monitor` with mode 0700 and its socket with mode 0600. It **fails without overwriting** an existing directory. The discovery client uses `lstat` to reject symlinks and checks current-user ownership and private permissions before forwarding. Normal monitor exit unlinks only its own socket inode and removes its own empty directory. A force-killed monitor can leave a stale directory that must be recovered explicitly; ISC never deletes someone else's files.

`send-hook --source ...` is a silent, best-effort client with a short connection timeout. It is not enabled in Claude Code or Codex. Same-UID discovery and receipt provide *no* proof that session completion or workload coverage is authoritative; automatic sleep remains unimplemented.

## Hook integration preview (no installation)

`hook-plan` reads the two user-level JSON hook files and displays only counts and proposed event names. It creates an absolute, cwd-independent ISC sender command for future append-only integrations, without installing or printing that command. Existing hook groups must never be replaced, reordered, disabled, or executed by this process. Codex's inline TOML hook state remains untouched. Any future apply/remove command requires a separate review, crash-safe backup, race-aware atomic write, idempotence, and source-specific approval. Codex can require user trust via `/hooks` before a new definition executes. Merely receiving a hook event does not establish real workload coverage.

## Explicit hook lifecycle management (opt-in)

`hook-enable` and `hook-disable` require `--source` and `--confirm`, operate on exactly one user-owned JSON target, use an ISC advisory lock, create a 0600 backup and write with a staging file and atomic rename. Existing hook groups are retained and remain in their original array order. Uninstallation removes only the exact ISC-generated group structure rather than overwriting later user edits. No install is implicit or performed by the project itself; the user chooses when to enable handlers. Concurrent external editors not participating in the lock remain a limitation; ISC compares the original file before replacement and refuses detectable conflicts.

ISC hook handlers use asynchronous command hooks except for `SessionEnd`, which is synchronous under Codex. The receiver remains monitor-only and has no authoritative proof of session ownership, completed child jobs, or full workload coverage. Codex trust review may be required.

## Persistent preference foundation (not a service)

`src/isc/settings.py` stores an explicit, minimal versioned JSON operator preference in a user-owned private configuration directory, using atomic writes and an advisory lock. The only supported values are `off` and `monitor`: missing settings default to `off`; corrupt, unsafe, or unexpected settings are rejected. `on` cannot be persisted until suspend safety requirements have been met. These values are presently **configuration preferences**, not an active background daemon control. `monitor` must still be invoked manually and terminates when its foreground duration ends. No GNOME settings are changed by this foundation.

## Controlled foreground MONITOR supervisor

`isc serve --seconds 0` starts a discoverable `LocalMonitor` **only** if the saved preference is MONITOR. The supervisor polls the persisted preference at bounded intervals, accepts only privacy-minimal event metadata and exits when OFF is selected or the configuration becomes invalid. A bounded `--seconds` run is available for testing. The service cannot suspend the computer, execute coding agents, or modify GNOME/logind settings. No systemd unit is installed or enabled, and startup on boot is not yet implemented. Abrupt termination may leave a stale private socket directory, which is never silently deleted.

## Explicit manual Sleep / Suspend (operator initiated only)

Manual power is separate from automatic ON/OFF/MONITOR. A user explicitly invoking `isc suspend --confirm` from a local interactive terminal, typing `SUSPEND` after reading the warning, may request Linux suspend independently of the saved automatic preference. `sleep` is an alias for the exact `systemctl suspend` mechanism, **not** the policy-selected `systemctl sleep` operation. The implementation reads logind `CanSuspend`, inhibitors and a limited candidate inventory, fails closed on unavailable probes, rechecks before submission, then issues `systemctl --check-inhibitors=yes suspend` without a shell. A request cannot override systemd inhibitors; a successful systemctl return means the request was accepted, not that resume worked. The user should save work and consider hardware wake/resume reliability before confirming. No automatic decision engine or daemon calls this function; hibernate remains out of scope for now.
