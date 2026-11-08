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
- [x] Read-only bounded logind active/inactive/unknown session census without identity disclosure (not multi-session GNOME idle coverage)
- [x] Display partial logind session counts within read-only sleep assessment; do not infer whole-host idle or session safety
- [x] Read-only current-user Linux /proc candidate inventory
- [x] Read-only process identity/descendant snapshots with PID-reuse protection and conservative lifecycle comparison
- [x] Independent 30-second MONITOR-only process snapshots with aggregate receiver health, freshness limits and UNKNOWN on errors
- [x] Same-user read-only /proc CPU-tick progress tracking for selected process identities with PID-reuse protection; MONITOR aggregates indicate execution only, never authenticated task ownership, completion, or safe idleness
- [x] Opt-in in-process lifecycle-claim correlation with previously enrolled native service process evidence: external waiting remains busy; stop, permission, human-wait and finish claims remain UNKNOWN; no socket registration, owner authentication, completion or automatic suspend
- [ ] Multi-scheduler continuous observation and trusted job-owner verification
- [x] Read-only on-demand systemd active-service discovery outside effective timer Unit= links, including independent service counts; no complete job inventory, authenticated ownership or automatic suspend
- [x] Periodic MONITOR-only independent-service aggregates with per-scope fail-closed sampling, stale health rejection and aggregate net-count changes; no service identifiers, completion claims or suspend authority
- [x] Pure bounded opt-in task↔process identity bindings with sticky UNKNOWN after missing scans, PID reuse, or vanished child (no authority or service wiring yet)
- [x] Refuse ambiguous duplicate process-to-task claims and retain UNKNOWN after process observation gaps (pure model only)
- [x] Preserve UNKNOWN when a previously observed descendant vanishes or changes identity, even if other children keep counts unchanged (pure model only)
- [x] Opt-in read-only NativeWorkSupervisor composes root/child lineage with service invocation/cgroup membership checks; partial evidence only, not running-service or authenticated-owner integration
- [x] Route MONITOR's existing process snapshots into a dormant, in-process opt-in native supervisor; no unauthenticated socket enrollment, no automatic task discovery, no suspend authority
- [x] Opt-in systemd MainPID verification: bracket service activation/cgroup identity and same-user stable process identity before in-process enrollment; no task-owner authentication or automatic discovery
- [x] Revalidate native enrollment against its original systemd invocation, process identity and cgroup on each opt-in observation; changed/missing evidence becomes sticky UNKNOWN (not application-level authentication)
- [x] Expose internal MONITOR systemd-verified enrollment method; no socket enrollment API, auto-discovery, power control or trusted owner claim
- [x] Read-only cgroup v2 membership probe binds a previously observed same-user process identity to an expected service cgroup; UNKNOWN on races (not trusted owner authentication)
- [x] Pure correlation of service invocation identity and cgroup membership observations; rejects changed activation, missing IDs or ambiguous service metadata (no authorization or runtime wiring)
- [x] Double-sample procfs cgroup membership around stable process identity checks; migration or missing evidence remains UNKNOWN
- [x] Bounded read-only cgroup-v2 subtree process inventory detects same-cgroup work despite PPID reparenting; incomplete snapshots are UNKNOWN and even empty results cannot prove completion
- [x] Integrate subtree counts into opt-in native supervisor; unexplained cgroup processes, incomplete census or count changes retain sticky UNKNOWN (no automatic enrollment or suspend authority)
- [x] Read-only bracketed service/process observer checks invocation and cgroup before and after process membership sampling; no task authority or continuous-service wiring
- [ ] Authenticated process-supervision/child-work accounting and trustworthy lifecycle state
- [x] Read-only logind inhibitor inventory
- [x] Read-only current GNOME session IsInhibited(4/8) flags (not complete multi-session coverage)
- [x] Include GNOME suspend/idle flag results in read-only check; UNKNOWN or inhibition blocks eligibility, other sessions remain unverified
- [x] Read-only Linux session and loaded systemd timer discovery with UNKNOWN on failures
- [x] On-demand, bounded read-only systemd timer waiting/running/activating service census without leaking unit names
- [x] On-demand, bounded read-only installed systemd timer-file census (separate from loaded units, no scheduler completeness claim)
- [x] Read effective Unit= associations for loaded timers without retaining unit names (configuration evidence only)
- [x] On-demand read-only systemd states for distinct timer-linked services (not proof of task completion or child-work quiescence)
- [x] Read-only systemd MainPID/ControlPID presence for timer-linked services (no process identity disclosure; zero does not prove quiescence)
- [x] Read-only systemd cgroup TasksCurrent presence for timer-linked services; unknown and zero remain non-authoritative
- [x] Bounded two-snapshot aggregate task-count comparison with unknown-state handling; unchanged totals are not lifecycle proof
- [x] Bounded identity-aware two-sample timer-linked service observation; only aggregate transitions returned, no job completion inference
- [x] Best-effort background systemd timer-linked service observer in continuous MONITOR receiver with fresh, partial aggregate health; never authorises sleep
- [x] Distinguish observed timer-linked service changes from unknown transitions in MONITOR health (no false activity claims)
- [x] Read-only on-demand systemd InvocationID identity comparison (does not establish task completion)
- [x] Continuous MONITOR scheduler samples InvocationID alongside task counts; missing IDs stay UNKNOWN, activation changes are reported
- [x] Publish separate aggregate service activation-change count in backwards-compatible MONITOR health; retains unknown transitions
- [ ] Resolve timer-to-service ownership using native metadata and verify actual job lifecycle/wake guarantees
- [ ] Scheduled service lifecycle coverage, all scheduler sources and conservative wake-safety checks
- [x] One-shot read-only `check` verdict with reason codes (continuous MONITOR and audit log pending)
- [x] Anonymous ordinal process candidate labels in safety output; no raw PIDs in reported work reasons
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
