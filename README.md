# Intelligent Sleep Controller (ISC)

**Local-first, application-independent sleep management for Linux.**

ISC aims to let a Linux computer sleep when autonomous work is **safely idle**, while keeping it awake during genuine work such as coding, compilation, inference, rendering, transfers, and backups.

ISC is a **standalone project**. It does not depend on Pandora, OpenClaw, Claude, Codex, Ollama, or any AI service. Application-specific integrations will be optional.

> **Development status — foundation only.** This branch implements conservative monitoring tools, an optional coding-agent hook sender, and persistent **OFF/MONITOR preferences**. It **does not install a daemon, change power settings, or automatically suspend a computer**. Manual Sleep/Hibernate, user-selected power profiles, display-only DPMS and timed or indefinite Keep Awake are explicit operator actions, never autonomous. ON and intelligent automatic suspend remain **not implemented**; optional login autostart for the MONITOR tray is available. An optional running tray can now supervise a local monitor-only process.

## Principles

- **Work state over CPU load.** A network request can be active even if CPU utilisation is near zero.
- **Multiple concurrent jobs.** A single active or unknown protected job keeps the computer awake.
- **Fail closed.** Unknown jobs, incomplete detection coverage, and unverified timers prevent automatic suspend.
- **Human blockers are different.** Waiting for a person's input can become sleep-eligible *only* after child work has finished and other safety conditions hold.
- **Explicit operator control.** Future modes: ON (safety-gated auto-suspend), OFF (no ISC-initiated auto-suspend), MONITOR (decisions without suspend), and temporary Keep Awake. Persistent settings and an easy desktop toggle are required.
- **Least privilege, offline operation.** No API keys, accounts, telemetry, or internet connection required for normal operation.
- **OS-native first; one power hub.** Reuse supported systemd-logind, kernel and GNOME facilities for existing power operations; ISC brings them together in one CLI/tray interface instead of duplicating them. Develop new functionality only where the operating system does not already provide an adequate mechanism.
- **The OS remains authoritative.** ISC must respect existing power policies, inhibitors, permissions and hardware capabilities. Manual Sleep, Keep Awake and Hibernate/Hybrid Sleep controls delegate actual operations to Linux; ISC supplies consistent access and safety checks.
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

Linux `Sleep` and `Suspend` commonly refer to **suspend to RAM**, so ISC uses the *same explicit action* for both. Do not confuse these aliases with `systemctl sleep`, which may choose a different sleep mechanism depending on logind policy. Hibernate-to-disk and Hybrid Sleep are distinct, capability-checked native Linux operations.

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

The menu exposes **OFF**, **MONITOR**, manual **Sleep/Suspend**, **Hibernate**, **Hybrid Sleep**, **Turn Off Displays**, system **Power Profile** selection, **Open System Power Settings**, **Keep Awake**, **Status**, and **Quit**. The ON option remains disabled because automatic suspend safety is not yet verified. Hibernate and Hybrid Sleep are conditional on this host's actual logind support and require typed confirmation. When MONITOR is selected in the running tray, ISC starts a **local, tray-owned monitor-only child** using `isc serve --seconds 0`. The tray displays whether its own receiver is running, starting, stopped, or unavailable. OFF stops the tray-owned monitor; Quit also stops it while preserving the stored preference for the next launch. The tray will not stop or replace a receiver started independently in another terminal. It does not autostart at login, install systemd units, or change power settings.

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

### Timed Keep Awake (explicit, cancellable, no daemon)

The ISC tray now offers **Keep Awake** presets of **15, 30, 60, 120 or
240 minutes**, plus **Cancel Keep Awake**. These are explicit user actions,
independent of the saved OFF/MONITOR preference. The tray reports the remaining
duration. The inhibitor is released on expiry, cancellation or when the tray
exits; it is never silently restored at login, including the indefinite choice.

The tray also offers **Keep Awake — Until Cancelled**, without a time limit, while ISC's tray is running. This setting is not currently restored automatically after the tray exits or after logout. The CLI offers the same operations in the **foreground**:

```bash
PYTHONPATH=src /usr/bin/python3 -m isc keep-awake
PYTHONPATH=src /usr/bin/python3 -m isc keep-awake --minutes 15
PYTHONPATH=src /usr/bin/python3 -m isc keep-awake --forever
```

The first command explains the available durations; the second requests
and holds a verified `systemd-inhibit --what=idle:sleep --mode=block`
inhibitor until the selected duration elapses, or indefinitely until explicitly cancelled when `--forever` is selected. **Keep the terminal open**;
Ctrl+C cancels the CLI lease immediately. The tray provides its own Cancel
menu item. These are separate user-owned foreground inhibitors; the CLI
cannot cancel a lease created in the tray or another terminal.

Keep Awake prevents systemd's idle/sleep operations while active, which
also means **manual Sleep/Suspend can be blocked** until it is cancelled.
It does **not** inhibit shutdown, does not prevent privileged overrides
or every possible low-level sleep path, and does not promise the display
will stay illuminated. ISC checks that its uniquely marked lock appears
in systemd before reporting success, instead of trusting a process PID.
If support or verification is unavailable, the command refuses rather
than claiming protection. No root access, persistent services, GNOME settings
changes, or background agent activity are required.

Keep Awake is not the future intelligent suspend decision engine; ISC's
automatic ON mode remains unavailable.

### Native Linux power controls (power profiles, displays, settings, hibernate)

ISC delegates these actions to existing OS facilities, without implementing a
separate power daemon. Everything can be accessed from both the tray and CLI.

| Action | CLI command | Native mechanism |
| --- | --- | --- |
| Show currently active/available power profiles | \`isc power-profiles\` | \`powerprofilesctl list/get\` |
| Select a supported performance mode | \`isc power-profiles --profile power-saver\` | \`powerprofilesctl set\` |
| Check display-only sleep support | \`isc display-off\` | X11 DPMS query |
| Turn off monitor images (PC keeps running) | \`isc display-off --confirm\` | \`xset dpms force off\` |
| Open the original GNOME power settings | \`isc power-settings\` | \`gnome-control-center power\` |
| Preview native Hibernate | \`isc hibernate\` | logind \`CanHibernate\` |
| Preview native Hybrid Sleep | \`isc hybrid-sleep\` | logind \`CanHybridSleep\` |

For Hibernate/Hybrid Sleep, \`--confirm\` must also be followed by an exact
typed confirmation (\`HIBERNATE\` or \`HYBRID\`) in an interactive terminal;
the tray uses an equivalent typed confirmation dialog. Unsupported host
capabilities, unknown inhibitors, or unverifiable state refuse the action.
The OS remains the final authority, including authorization and inhibitors.
A capability of \`yes\` does **not** prove that hibernate/resume will work on
this machine; actual hardware testing is a separate, user-controlled step.

**Turn Off Displays** is supported only in an Xorg desktop with X11 DPMS
enabled. It requests a low-power state for connected monitors; it does *not*
suspend the computer, stop ongoing processing, change system sleep settings,
or guarantee that the displays remain off when input devices move. Moving
the mouse or pressing a key normally wakes the screens. Wayland is reported
as unsupported rather than invoking X11 tools incorrectly.

A power profile is displayed or offered only if the local power-profile
service actually provides it. ISC does not install or impersonate that
service. Opening native Power settings reuses the operating system's
configuration window rather than creating a competing editor.

## Design and scope

- [Architecture and task-state contract](docs/ARCHITECTURE.md)
- [Safety and test requirements](docs/SAFETY.md)
- [Development roadmap](docs/ROADMAP.md)

## Contributions

The Linux power-management paths are hardware- and distribution-sensitive. Please accompany code changes with tests, avoid running automatic suspend on contributors' CI machines, and do not put personal host logs, repository credentials, or private project materials in issues or commits.

## Licence

Apache License 2.0. See [LICENSE](LICENSE).
