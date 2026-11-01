# Architecture — proposed contract

ISC is standalone, offline, and Linux-first. This is a **design document**, not a claim that the daemon or integrations already exist.

## Layers

1. **Adapters** report trusted lifecycle events from independent applications; app-specific packages are optional.
2. **Work registry** associates task IDs, process/child ownership, last-seen timestamps, and work states; lost heartbeats become UNKNOWN, **not** COMPLETED.
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
