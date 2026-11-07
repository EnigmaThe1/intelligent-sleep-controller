# Roadmap

## Engineering constraint — reuse Linux facilities

- [x] Adopt OS-native-first architecture: ISC is a common CLI/tray power-management hub, not a competing implementation.
- [ ] Audit each new control for existing logind/systemd, GNOME and kernel support before writing a new mechanism.
- [ ] Expose additional supported OS power controls (hibernate, hybrid sleep, etc.) only after capability and hardware safety verification.
- [ ] Keep command-line and tray operations backed by shared execution paths; preserve native inhibitors and permissions.
- [ ] Build custom autonomous scheduling only for demonstrable gaps in native task-aware idle detection.


## Phase 0 — repository foundation (this branch)

- [x] Apache 2.0 licence (already present in repository)
- [x] Standalone architecture and safety criteria
- [x] Pure fail-closed eligibility policy with unit tests
- [x] Read-only `smart-sleep status` CLI
- [x] Linux CI without actual suspend
- [x] GNOME desktop idle-time probe with fail-closed handling
- [ ] Review and merge

## Phase 1 — observability

- [x] Read-only GNOME D-Bus idle probe (initial standalone adapter)
- [x] Bounded in-memory work registry and retention with sticky UNKNOWN on lost history (authoritative resync pending)
- [ ] Multi-session user-idle and active-seat discovery
- [x] Read-only current-user Linux /proc candidate inventory
- [x] Read-only process identity/descendant snapshots with PID-reuse protection and conservative lifecycle comparison
- [x] Independent 30-second MONITOR-only process snapshots with aggregate receiver health, freshness limits and UNKNOWN on errors
- [ ] Multi-scheduler continuous observation and trusted job-owner verification
- [ ] Authenticated process-supervision/child-work accounting and trustworthy lifecycle state
- [x] Read-only logind inhibitor inventory (GNOME session inhibitors still pending)
- [x] Read-only Linux session and loaded systemd timer discovery with UNKNOWN on failures
- [x] On-demand, bounded read-only systemd timer waiting/running/activating service census without leaking unit names
- [ ] Resolve timer-to-service ownership using native metadata and verify actual job lifecycle/wake guarantees
- [ ] Scheduled service lifecycle coverage, all scheduler sources and conservative wake-safety checks
- [x] One-shot read-only `check` verdict with reason codes (continuous MONITOR and audit log pending)
- [x] Explicit foreground MONITOR supervisor with persistent OFF shutdown and safe invalid-config failure
- [x] Optional systemd --user MONITOR supervisor with bounded recovery and OFF idleness (not installed by code changes)
- [x] Private stale-socket recovery with exclusive ownership and foreign-path protection
- [x] Bounded socket read deadline, error handling and limited unknown-event counter cardinality
- [x] Same-user aggregate health status and event freshness (not authoritative coverage)
- [ ] Trusted workload-coverage integrity, timer/wake verification, and minimal local audit log
- [ ] Tests using synthetic system states; zero suspend actions

## Phase 2 — controlled power management

- [x] Persistent OFF/MONITOR user preference (no daemon or power effects)
- [ ] Safe ON operator control and enforced mode transitions in a persistent service
- [x] Explicit manual Sleep/Suspend preview, capability/inhibitor checks and typed confirmation (no CI suspend)
- [x] Native capability-gated manual Hibernate and Hybrid Sleep preview/explicit request (hardware resume tests pending)
- [ ] Hardware suspend, hibernate and hybrid resume verification on non-critical test systems
- [x] Explicit timed Keep Awake via verified systemd inhibitor (tray presets + CLI foreground; cancel/expiry)
- [x] Explicit indefinite Keep Awake until Cancelled while tray/CLI is running
- [x] Keep Awake owner-crash release using parent-pipe EOF and native systemd inhibition
- [x] Controlled second-timeout handling in Keep Awake cancellation
- [ ] Cross-process shared Keep Awake ownership or deliberate persistence across tray restarts
- [ ] Safe inhibitor lifecycle and privileged suspend executor
- [ ] Reversible GNOME settings ownership, recovery and uninstall
- [ ] Race-safe pre-suspend check; opt-in only
- [ ] Suspend/resume integration tests on non-critical hardware

## Phase 2A — Native power hub controls

- [x] Query and select only the host-supported system power profiles using powerprofilesctl.
- [x] Open GNOME's existing Power settings panel (no alternative settings editor).
- [x] Display-only sleep for Xorg via xset DPMS without suspending processing.
- [ ] Display-only sleep backend for non-X11 desktops (Wayland support only if compositor exposes a safe API).
- [ ] Verify native power-profile and DPMS behaviour on representative desktop systems.

## Phase 3 — optional adapters

- [x] Pure vendor-independent lifecycle event interpreter (no transport or installed hooks)
- [x] Strict privacy-minimal hook payload parser and synthetic read-only CLI (no installation)
- [x] Ephemeral same-user Unix-socket event transport, aggregate event counters, and bounded foreground monitor (not authoritative coverage)
- [x] Opt-in temporary private socket discovery and non-blocking hook relay (vendor configurations untouched)
- [ ] Persistent authenticated/supervised adapter transport with integrity and loss detection
- [x] Read-only Claude Code and Codex hook integration plan (no config modifications)
- [x] Shared hook recognition across plan and installer; legacy hook idempotency and safe removal
- [x] Optional single-source append-only hook enable/disable with private backups, isolated tests (not yet applied on user host)
- [ ] Trusted agent-origin verification and authenticated process supervision
- [ ] OpenClaw, Ollama, build/test tools, transfer jobs, backup jobs
- [x] Conservative subagent start/stop observation preserves child-unknown flag
- [ ] Authoritative active-child counts and external-wait supervision; no idle assumptions from CPU
- [x] Optional GTK3/AppIndicator status tray with OFF/MONITOR and guarded manual Suspend (no autostart)
- [x] Explicit current-user GNOME Show Apps launcher with separate visible GTK window and reversible installation
- [ ] Full desktop packaging with automatic desktop-entry registration and icon asset on supported distros
- [x] Tray-owned MONITOR process starts/stops with persistent OFF/MONITOR choices (not login autostart)
- [x] Optional per-user autostart enable/disable with explicit approval, foreign-file protection and single-instance tray lock (not enabled on host)
- [x] Legacy ISC systemd/autostart path-aware ownership validation for safe disable after moves
- [x] Optional monitor-only recovery and aggregate health status
- [ ] Authoritative multi-application coverage and agent crash recovery

## Phase 4 — portability and releases

- [ ] Supported distro matrix, packaging, documentation, security review
- [ ] Hardware-specific sleep and wake regression tests
- [ ] Public release process with repeatable builds and reproducible evidence

No feature requiring privileged changes should be rushed into Phase 0.
