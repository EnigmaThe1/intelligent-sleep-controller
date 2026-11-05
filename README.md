# Intelligent Sleep Controller (ISC)

**Local-first, application-independent sleep management for Linux.**

ISC aims to let a Linux computer sleep when autonomous work is **safely idle**, while keeping it awake during genuine work such as coding, compilation, inference, rendering, transfers, and backups.

ISC is a **standalone project**. It does not depend on Pandora, OpenClaw, Claude, Codex, Ollama, or any AI service. Application-specific integrations will be optional.

> **Development status — foundation only.** This branch implements conservative monitoring tools, an optional coding-agent hook sender, and persistent **OFF/MONITOR preferences**. It **does not install a daemon, change power settings, hold inhibitors, or suspend a computer**. ON, automatic suspend, and login-started background monitoring are **not implemented**. An optional running tray can now supervise a local monitor-only process.

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

The commands are read-only. `check` explains why the host **must not be automatically suspended** until job coverage and timer/wake safety are verified. A pure in-memory task-lease registry now exists for future trusted adapters; the read-only `processes` command lists selected candidate process names/PIDs without reading arguments, paths, or environment variables. Process discovery **cannot** determine whether an agent is blocked or running, nor does it establish workload coverage. A pure event interpreter maps lifecycle observations conservatively and only permits WAITING_HUMAN after an adapter explicitly verifies child-work quiescence. **No vendor hooks or persistent daemon are installed.** A foreground, private, same-user Unix-socket monitor can accept minimal metadata for time-bounded experiments. Transport events are **not authoritative proof of workload coverage**.

### Hook-payload parser (synthetic input only)

`hook-probe` accepts a bounded JSON object on standard input and prints only the source, event name, and conservative state. It never prints session IDs, tool arguments, prompt content, paths, responses, or secrets:

```bash
printf '%s\n' '{"session_id":"synthetic-local-test","hook_event_name":"Stop","tool_input":{"command":"never executed"}}' \
  | PYTHONPATH=src python3 -m isc hook-probe --source codex
```

**Do not register this diagnostic command as an agent hook.** It is deliberately not an event receiver, always read-only, and cannot establish authoritative task/child coverage. The future non-blocking hook transport will need to make loss of events visible without affecting agents.

### Local foreground receiver (no hook installation)

On a Linux user session with a private `XDG_RUNTIME_DIR`, the following starts a **temporary foreground listener** and sends one internally generated synthetic `Stop` event:

```bash
PYTHONPATH=src python3 -m isc monitor --seconds 1 --self-test
```

The default socket is placed inside a new private runtime directory and removed after the monitor exits. A separate `send-hook --source codex --socket-path <path>` command can relay a bounded, sanitised JSON hook message from standard input, **but it is not installed in any agent**.

For safe cross-process discovery, `monitor --advertise` creates a *predictable, private* temporary socket at `$XDG_RUNTIME_DIR/isc-monitor/events.sock`. This refuses to replace any existing directory, never alters a vendor configuration, and removes its own socket and directory on normal exit. `send-hook --source codex` can then discover the temporary socket automatically and returns success silently even when no listener exists. An example test uses `PYTHONPATH=src python3 -m isc monitor --seconds 1 --self-test --advertise`. No receiver is installed as a background service. The foreground monitor reports only safe aggregate event-type counters (for example, `claude_code/SessionStart: 1`); it never prints session identifiers, hook commands, arguments, or private content. Any transport failure returns success to the calling application, and it never writes to stdout. Receiving an event does not certify its origin beyond the operating-system user or make sleep eligible.

This temporary foreground experiment is **not** the future persistent ON/OFF/MONITOR configuration. Automatic suspension and service installation remain unimplemented.

### Preview optional Claude Code and Codex integration (read-only)

```bash
PYTHONPATH=src python3 -m isc hook-plan
```

This inspects **only** the structure of existing user-level `~/.claude/settings.json` and `~/.codex/hooks.json`, and reports which supported event groups ISC would need. It never prints existing handler commands, application secrets, prompts or other configuration values. It does not write or install anything, does not open a socket and does not run either coding agent. `~/.codex/config.toml` is deliberately **not modified**. A separate opt-in implementation and trust review are required to install hooks safely; Codex may require the user to approve the new hook definition in `/hooks`.

### Explicit opt-in hook installation (not run automatically)

A separate command can add ISC-only handler groups **after** you explicitly authorize one source using `--confirm`, or remove those exact groups:

```bash
PYTHONPATH=src python3 -m isc hook-enable --source codex --confirm
PYTHONPATH=src python3 -m isc hook-disable --source codex --confirm
```

Do **not** run these until you've reviewed the tested plan and confirmed that ISC's monitor-only hooks should be enabled. Each modification produces a private (0600) recovery backup in the agent's configuration directory. It leaves existing hook groups in place and appends ISC groups, or removes only exact ISC-created groups. The target is rewritten atomically with a best-effort last-moment race check. External editors that ignore ISC's advisory lock could still race; refrain from modifying the same file concurrently. It never executes either coding agent or enables sleep. Codex may require separate hook trust review. For the safest uninstall, use the targeted `hook-disable`, not a whole-file backup restoration that could discard later settings.

### Persistent operator preference (no service, no suspend)

The preference is kept in a local, user-owned JSON file beneath `$XDG_CONFIG_HOME/intelligent-sleep-controller/settings.json` (or `~/.config/intelligent-sleep-controller/settings.json`). It survives reboots, and starts at the conservative **OFF** default if there is no configuration. A missing or invalid configuration never enables automatic suspend.

```bash
PYTHONPATH=src python3 -m isc mode
PYTHONPATH=src python3 -m isc mode --set monitor
PYTHONPATH=src python3 -m isc mode --set off
```

`mode --set on` explicitly refuses until automatic suspend is implemented, safety checks pass, and operator approval is possible. The preference alone does **not** start or stop a monitor, alter GNOME/logind settings, or install a background service. `monitor` remains an explicit, temporary foreground diagnostic independent of the saved preference. A future service will enforce the preference, including changes while running.

### Controlled foreground MONITOR service (opt-in, not installed)

ISC can now keep an explicitly launched **foreground** receiver running under the saved MONITOR preference. Its socket is private and discoverable by previously installed optional hooks. Switching the preference to OFF stops the receiver after a short poll interval; corrupted settings also cause shutdown without any suspend operation.

```bash
PYTHONPATH=src python3 -m isc mode --set monitor
PYTHONPATH=src python3 -m isc serve --seconds 30
PYTHONPATH=src python3 -m isc mode --set off
```

`serve --seconds 0` keeps the foreground monitor running until OFF or Ctrl+C. Only invoke it in a terminal you intend to dedicate to monitoring. It is **not automatically started** at login and is **not yet installed as a systemd user service**. Startup supervision, crash recovery, adapter completeness, and true safe-suspend decisions are future work. Mode ON remains prohibited.

### Manual Sleep / Suspend (explicit user action only)

Linux `Sleep` and `Suspend` commonly refer to **suspend to RAM**, so ISC uses the *same explicit action* for both. Do not confuse these aliases with `systemctl sleep`, which may choose a different sleep mechanism depending on logind policy. Hibernate-to-disk is a separate future action.

```bash
PYTHONPATH=src python3 -m isc power
PYTHONPATH=src python3 -m isc suspend
PYTHONPATH=src python3 -m isc sleep
```

These commands only display options and perform a read-only preflight. **No sleep is requested without `--confirm` AND typing `SUSPEND` in a local interactive terminal.** For an eventual deliberate suspend, the operator invokes `PYTHONPATH=src python3 -m isc suspend --confirm`, then enters the required confirmation. This may immediately interrupt active work, so save documents and check running tasks first. ISC checks logind capability, inhibitor locks and an illustrative local process inventory both before and after the confirmation. Unknown capability, inhibitors, or inventory refuse the operation; the OS is also required to check inhibitors when queuing suspend. It never uses `--force`, disables inhibitors, or alters GNOME's power settings.

**Manual operation is distinct from automatic ISC modes.** A deliberate manual suspend remains possible in OFF or MONITOR; selecting OFF only disables ISC-initiated **automatic** suspend. ON is still not implemented, and ISC still cannot assert coverage of all background work. The hardware can have resume problems even when the OS accepts a suspend request. Code and CI tests use mocks and never sleep a test machine.

### Optional system-tray icon (GNOME/AppIndicator)

For a status-area menu like Ubuntu top-bar AppIndicator icons, ISC includes an optional **GTK3 + AyatanaAppIndicator** frontend. The CLI remains independent of all graphical libraries. Test local availability without opening the tray or installing anything:

```bash
PYTHONPATH=src /usr/bin/python3 -m isc tray-check
```

If the diagnostic reports missing bindings, Ubuntu packages commonly include `python3-gi`, `gir1.2-gtk-3.0`, and `gir1.2-ayatanaappindicator3-0.1`. GNOME Shell needs an enabled AppIndicator/KStatusNotifierItem extension for the icon to appear.

To launch in the logged-in graphical session:

```bash
PYTHONPATH=src /usr/bin/python3 -m isc tray
```

The menu exposes **OFF**, **MONITOR**, a **Sleep / Suspend…** dialog, **Status…**, and **Quit**. ON, Keep Awake, Hibernate and Hybrid Sleep are deliberately **disabled** until implemented. When MONITOR is selected in the running tray, ISC starts a **local, tray-owned monitor-only child** using `isc serve --seconds 0`. The tray displays whether its own receiver is running, starting, stopped, or unavailable. OFF stops the tray-owned monitor; Quit also stops it while preserving the stored preference for the next launch. The tray will not stop or replace a receiver started independently in another terminal. It does not autostart at login, install systemd units, or change power settings.

The tray-managed child inherits only the environment variables needed to read settings and create the local socket—not unrelated API keys or agent secrets. It is started only in response to the persisted MONITOR setting while the tray is running. A monitor that crashes unexpectedly is not automatically respawned in a loop; the status instead reports the failure. No workload-completeness claims follow from a running receiver.

Manual Suspend performs a read-only OS preflight, warns about background work and potential display-resume problems, then requires an explicit dialog click **and typing SUSPEND**. It repeats preflight immediately before requesting `systemctl suspend` with inhibitor checks. Merely opening the menu, changing OFF/MONITOR, or opening Status cannot suspend the machine. Actual suspend/resume on the development desktop remains a deliberate user decision.

### Optional start-at-login registration (disabled by default)

ISC can install **only its own** per-user desktop autostart entry under
`~/.config/autostart/intelligent-sleep-controller.desktop` (or the
equivalent XDG configuration directory). The entry invokes the optional ISC
tray in the logged-in GNOME session. It does not install a system service,
require root, override GNOME settings, or enable automatic suspension.

```bash
PYTHONPATH=src /usr/bin/python3 -m isc autostart
PYTHONPATH=src /usr/bin/python3 -m isc autostart --startup enable --confirm
PYTHONPATH=src /usr/bin/python3 -m isc autostart --startup disable --confirm
```

The first command is read-only. Installation and removal require explicit
`--confirm`; they never start/stop a currently running tray. ISC refuses
to overwrite or remove a different entry occupying the filename, and keeps
the startup file private. An enabled entry starts at the **next desktop login**,
not immediately. If MONITOR is the saved preference, the newly launched tray
will start a monitor-only receiver; OFF does not start one. This option does
not turn on automatic power management.

The tray also uses a private session lock to avoid duplicate tray icons and
competing monitoring children. Other applications are never terminated.

## Design and scope

- [Architecture and task-state contract](docs/ARCHITECTURE.md)
- [Safety and test requirements](docs/SAFETY.md)
- [Development roadmap](docs/ROADMAP.md)

## Contributions

The Linux power-management paths are hardware- and distribution-sensitive. Please accompany code changes with tests, avoid running automatic suspend on contributors' CI machines, and do not put personal host logs, repository credentials, or private project materials in issues or commits.

## Licence

Apache License 2.0. See [LICENSE](LICENSE).
