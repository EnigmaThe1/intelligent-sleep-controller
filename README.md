# Intelligent Sleep Controller (ISC)

**Local-first, application-independent sleep management for Linux.**

ISC aims to let a Linux computer sleep when autonomous work is **safely idle**, while keeping it awake during genuine work such as coding, compilation, inference, rendering, transfers, and backups.

ISC is a **standalone project**. It does not depend on Pandora, OpenClaw, Claude, Codex, Ollama, or any AI service. Application-specific integrations will be optional.

> **Development status — foundation only.** This branch implements a pure, conservative decision policy, a read-only status command, and tests. It **does not install a daemon, change power settings, hold inhibitors, or suspend a computer**. ON/OFF/MONITOR controls and automated suspend are *planned*, not available yet.

## Principles

- **Work state over CPU load.** A network request can be active even if CPU utilisation is near zero.
- **Multiple concurrent jobs.** A single active or unknown protected job keeps the computer awake.
- **Fail closed.** Unknown jobs, incomplete detection coverage, and unverified timers prevent automatic suspend.
- **Human blockers are different.** Waiting for a person's input can become sleep-eligible *only* after child work has finished and other safety conditions hold.
- **Explicit operator control.** Future modes: ON (safety-gated auto-suspend), OFF (no ISC-initiated auto-suspend), MONITOR (decisions without suspend), and temporary Keep Awake. Persistent settings and an easy desktop toggle are required.
- **Least privilege, offline operation.** No API keys, accounts, telemetry, or internet connection required for normal operation.
- **Reversible installation.** Future installers must preserve and restore prior GNOME/systemd power settings.
- **No assumptions about unrelated applications.** Unsupported tasks must not be silently classified idle.

## Try the foundation (non-invasive)

Requires Python 3.10+; no third-party runtime packages:

```bash
PYTHONPATH=src python3 -m isc status
PYTHONPATH=src python3 -m isc idle
PYTHONPATH=src python3 -m isc inhibitors
PYTHONPATH=src python3 -m isc processes
PYTHONPATH=src python3 -m isc check
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

The commands are read-only. `check` explains why the host **must not be automatically suspended** until job coverage and timer/wake safety are verified. A pure in-memory task-lease registry now exists for future trusted adapters; the read-only `processes` command lists selected candidate process names/PIDs without reading arguments, paths, or environment variables. Process discovery **cannot** determine whether an agent is blocked or running, nor does it establish workload coverage. A pure event interpreter maps lifecycle observations conservatively and only permits WAITING_HUMAN after an adapter explicitly verifies child-work quiescence. **No vendor hooks, daemon, trusted event transport, or live adapter event collection are installed.**

### Hook-payload parser (synthetic input only)

`hook-probe` accepts a bounded JSON object on standard input and prints only the source, event name, and conservative state. It never prints session IDs, tool arguments, prompt content, paths, responses, or secrets:

```bash
printf '%s\n' '{"session_id":"synthetic-local-test","hook_event_name":"Stop","tool_input":{"command":"never executed"}}' \
  | PYTHONPATH=src python3 -m isc hook-probe --source codex
```

**Do not register this diagnostic command as an agent hook.** It is deliberately not an event receiver, always read-only, and cannot establish authoritative task/child coverage. The future non-blocking hook transport will need to make loss of events visible without affecting agents.

## Design and scope

- [Architecture and task-state contract](docs/ARCHITECTURE.md)
- [Safety and test requirements](docs/SAFETY.md)
- [Development roadmap](docs/ROADMAP.md)

## Contributions

The Linux power-management paths are hardware- and distribution-sensitive. Please accompany code changes with tests, avoid running automatic suspend on contributors' CI machines, and do not put personal host logs, repository credentials, or private project materials in issues or commits.

## Licence

Apache License 2.0. See [LICENSE](LICENSE).
