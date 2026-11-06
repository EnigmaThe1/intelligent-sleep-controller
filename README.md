# Intelligent Sleep Controller (ISC)

**Local-first, application-independent sleep management for Linux.**

ISC aims to let a Linux computer sleep when autonomous work is **safely idle**, while keeping it awake during genuine work such as coding, compilation, inference, rendering, transfers, and backups.

ISC is a **standalone project**. It does not depend on Pandora, OpenClaw, Claude, Codex, Ollama, or any AI service. Application-specific integrations will be optional.

> **Development status — safety-gated preview.** ISC provides native manual power controls, timed/indefinite Keep Awake, persistent OFF/MONITOR, a desktop tray, privacy-limited agent observations, and an **optional** systemd user monitor service. The full automatic-suspend ON path is **not enabled or considered safe**: verified coverage of independent background workloads and scheduled jobs, hardware resume testing, and protected final suspend checks are still outstanding. Installing the code or enabling MONITOR never requests suspend.

## Principles

- **Work state over CPU load.** A network request can be active even if CPU utilisation is near zero.
- **Multiple concurrent jobs.** A single active or unknown protected job keeps the computer awake.
- **Fail closed.** Unknown jobs, incomplete detection coverage, and unverified timers prevent automatic suspend.
- **Human blockers are different.** Waiting for a person's input can become sleep-eligible *only* after child work has finished and other safety conditions hold.
- **Explicit operator control.** Future modes: ON (safety-gated auto-suspend), OFF (no ISC-initiated auto-suspend), MONITOR (decisions without suspend), and temporary or indefinite Keep Awake. Persistent settings and an easy desktop toggle are required.
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

The menu exposes **OFF**, **MONITOR**, manual **Sleep/Suspend**, **Hibernate**, **Hybrid Sleep**, **Turn Off Displays**, system **Power Profile** selection, **Open System Power Settings**, **Keep Awake**, **Status**, and **Quit**. The ON option remains disabled because automatic suspend safety is not yet verified. Hibernate and Hybrid Sleep are conditional on this host's actual logind support and require typed confirmation. When MONITOR is selected in the running tray, ISC starts a **local, tray-owned monitor-only child** using `isc serve --seconds 0`. The tray displays whether its own receiver is running, starting, stopped, or unavailable. OFF stops the tray-owned monitor; Quit also stops it while preserving the stored preference for the next launch. The tray will not stop or replace a receiver started independently in another terminal. The tray supports separate, explicit user-level autostart registration; it does not install systemd units or change OS power settings merely by launching. Selecting a native power profile deliberately changes the operating system's current profile.

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

### Optional independent monitoring service (systemd --user)

The tray can supervise a monitor-only receiver, but it is not required for
headless monitoring. ISC now also supports an **optional** native systemd
user service that observes the saved OFF/MONITOR preference. It does not
suspend the host, install a system unit, or require root privileges.

```bash
PYTHONPATH=src /usr/bin/python3 -m isc service
PYTHONPATH=src /usr/bin/python3 -m isc service --service enable --confirm
PYTHONPATH=src /usr/bin/python3 -m isc service --service disable --confirm
```

The first command reports installation/enabled/active status without making
changes. Enable/disable are explicit operations using systemd's standard user
service manager. The unit is private to the current user, and ISC never
overwrites a foreign unit with the same name. It retries failed monitoring
with bounded backoff, recovers only verified stale ISC sockets, and leaves a
healthy receiver owned by another ISC session untouched.

MONITOR selects observation, not power management; OFF causes the standalone
supervisor to remain idle without collecting activity. A running receiver does
not prove workload coverage. The CLI `isc status` and tray Status dialog
expose aggregate event health, freshness and UNKNOWN counts without revealing
task names, prompts or session identifiers. The service is **not enabled by
installing or updating ISC**. Enabling it may briefly overlap with a currently
running tray-managed monitor: IPC ownership ensures only one receives events.

**Native user-service installation is a change to desktop startup behaviour**
and should be explicitly approved and checked on the actual machine.

### Timed Keep Awake (explicit, cancellable, no daemon)

The ISC tray now offers **Keep Awake** presets of **15, 30, 60, 120 or
240 minutes**, plus **Cancel Keep Awake**. These are explicit user actions,
independent of the saved OFF/MONITOR preference. The tray reports the remaining
duration. The inhibitor is released on expiry, cancellation, when the tray exits, or
after an unexpected owner-process death. It is never silently restored at
login, including the indefinite choice.

The tray also offers **Keep Awake — Until Cancelled**, without a time limit, while ISC's tray is running. This setting is not currently restored automatically after the tray exits or after logout. The CLI offers the same operations in the **foreground**:

```bash
PYTHONPATH=src /usr/bin/python3 -m isc keep-awake
PYTHONPATH=src /usr/bin/python3 -m isc keep-awake --minutes 15
PYTHONPATH=src /usr/bin/python3 -m isc keep-awake --forever
```

The first command explains the available durations; the second requests
and holds a verified `systemd-inhibit --what=idle:sleep --mode=block`
inhibitor until the selected duration elapses, or indefinitely until cancelled
when `--forever` is selected. A small child worker watches a private pipe
from its owning process; if the tray or CLI dies, pipe EOF releases the
inhibitor rather than leaving an orphaned indefinite lock. **Keep the terminal open**;
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

## Running from a package or source checkout

When installed from a Python package, ISC provides the `isc` command
and the older `smart-sleep` alias. From an uninstalled source checkout,
use `PYTHONPATH=src /usr/bin/python3 -m isc <command>`. The optional
GTK/AppIndicator dependencies are provided by the Linux distribution.

The source code and CI tests alone cannot validate hardware suspend/resume,
systemd user-service integration, or the reliability of every third-party
application adapter. Those require explicit host-level verification.

## Diagnosing optional coding-agent hook delivery

`isc hook-plan` lists whether ISC hook groups match their current
configuration. A configured hook does **not** establish that an agent has
actually executed it or that an event reached ISC.

To test the local sender and the running MONITOR receiver without starting an
agent or editing its configuration:

```bash
PYTHONPATH=src /usr/bin/python3 -m isc hook-self-test --source claude_code
```

This sends a reserved **synthetic IscSelfTest event** through ISC's normal
silent `send-hook` command, then checks the receiver's aggregate accepted
counter. Updated receivers count these requests separately and **do not
register them as workload activity**. Older receivers treat unrecognised events
as UNKNOWN; neither behavior can authorize suspend. The test does not run
Claude Code, request power management, create files or install hooks. Live
unrelated events may increment the aggregate counter, so a successful test
confirms a usable local route but **not** actual third-party hook execution
or complete workload coverage.

`isc status` and the tray's Status dialog now show **non-probe events claimed
by Claude Code and Codex separately**, plus synthetic diagnostic counts.
These are local sender claims, **not authenticated application identities**.
No prompts, filenames, raw session identifiers, or full task details are
collected in these counters. After updating the code, the already-running
user monitor service must be restarted to load the new receiver protocol;
until then per-source information is shown as unavailable. Restarting resets
the service's in-memory event counts.

If the local route works but live event counts remain zero during genuine
agent use, inspect the application-specific hook execution and environment
before changing configuration; do not treat this as evidence of idleness.
Codex hooks remain optional and are never enabled automatically.

## Native scheduled-work and session discovery (read only)

A separate diagnostic command queries standard OS interfaces and returns
aggregate counts without storing user names, unit names or command arguments:

```bash
PYTHONPATH=src /usr/bin/python3 -m isc native-activity
```

It inspects `loginctl list-sessions` and loaded timer units from
`systemctl --system` and `systemctl --user`. If an OS query fails or its
output cannot be verified, its result is **UNKNOWN**, not zero.

This is **partial discovery only**: loaded timer units do not cover all
scheduled work, cron/at jobs, external automation, unobserved user services,
independent application schedules, or what will happen after the machine
suspends. The presence of sessions is not proof of current human activity.
The result must never set `coverage_complete` or
`scheduled_work_safe` in ISC's eligibility policy.

## Reliability hardening (MONITOR-only)

The monitor now limits in-memory lease storage and event-name cardinality.
Expired leases are retained temporarily for diagnostics and eventually
pruned. **Loss of tracking history is sticky UNKNOWN**, not a completed
workload or permission to suspend. Source and diagnostic counters do not
store prompts, commands, filenames, or raw session identifiers.

Each Unix-socket client has an overall time budget; a slow sender cannot
indefinitely occupy the single-threaded receiver. Receiver accept errors
are handled without treating them as successful work observations.

ISC hook planning and installation now recognise the same canonical ISC
command structure across supported legacy async/timeout variants and previous
Python/source locations. Installation is idempotent, and disabling removes
only ISC command handlers, leaving unrelated handlers and group metadata.
Legacy ownership requires the complete canonical command form, including
restricted absolute paths and an exact ISC entry point; arbitrary shell
commands or lookalike fragments are not accepted. Existing agent hooks are
not modified by upgrading ISC.

A systemd user unit or GNOME autostart entry created by an older ISC
interpreter/source path can still be **disabled** after the installation is
moved, provided its entire expected structure is preserved. Enabling a
legacy entry with a stale path is refused until the operator deliberately
disables the previous entry and installs the current one. Foreign or
modified units are not removed.

Keep Awake cancellation now handles a second subprocess timeout without
propagating an uncaught TimeoutExpired exception. Any escalation is restricted
to the verified ISC-owned process group.

Linux process discovery reports the count of process names that could not
be decoded. A single invalid UTF-8 process name no longer invalidates the
entire candidate inventory. Incomplete visibility is explicitly disclosed
and remains unsuitable for automatic sleep eligibility.

Subagent start/stop observations retain an unverified child-work flag
within the session lease. A single child stop does not prove all child work
is finished. Source events still cannot authenticate their originating
application. Automatic ON remains disabled.

Existing private hook-configuration backups are **not automatically
deleted**. Safe backup rotation remains a future operator-approved
maintenance feature; do not remove recovery evidence during upgrades.

## Read-only process lifetime observation

ISC can compare two bounded, same-user Linux process snapshots without
reading process arguments, environment, open files, project content, or logs:

```bash
PYTHONPATH=src /usr/bin/python3 -m isc process-watch --seconds 5
```

The scanner selects a **limited set of recognizable work process names** and
follows their visible same-user child processes, including children with
different executable names. It uses both the PID and the kernel's process
start-time ticks from `/proc/PID/stat` so a recycled PID is not treated as
the same continuing job. The report distinguishes continuing, newly observed,
disappeared, and PID-reused processes; it does not publish process arguments
or session details. The standalone `process-watch` command is explicitly
invoked and limited to a 60-second observation window.

When ISC owns the **persistent MONITOR** receiver, a separate lightweight
read-only thread also samples the process table every 30 seconds. This does not
run when the operator selects OFF, and the thread exits when the monitor stops.
The socket receiver remains responsive while a scan runs. `isc status`
reports only aggregate root/descendant counts, observed visibility races,
identity transitions and sample age. Failed or stale scans return **UNKNOWN**;
the user can distinguish missing data from an observed count of zero.
This service integration never starts or terminates an application.

**Critical limitation:** Disappearance is not proof of task completion or
successful output; a process may crash, fork, change identity, or disappear
between samples. Visibility to other users' work and unobserved processes is
not complete. Neither successful polling nor an empty candidate list means
automatic suspension is safe. ON remains disabled.

## Design and scope

- [Architecture and task-state contract](docs/ARCHITECTURE.md)
- [Safety and test requirements](docs/SAFETY.md)
- [Development roadmap](docs/ROADMAP.md)

## Contributions

The Linux power-management paths are hardware- and distribution-sensitive. Please accompany code changes with tests, avoid running automatic suspend on contributors' CI machines, and do not put personal host logs, repository credentials, or private project materials in issues or commits.

## Licence

Apache License 2.0. See [LICENSE](LICENSE).
