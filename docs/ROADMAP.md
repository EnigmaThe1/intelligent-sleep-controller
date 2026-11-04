# Roadmap

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
- [x] In-memory work registry, lease contract, and TTL expiry -> UNKNOWN (adapter transport/persistence pending)
- [ ] Multi-session user-idle and active-seat discovery
- [x] Read-only current-user Linux /proc candidate inventory (lifecycle classification pending)
- [x] Read-only logind inhibitor inventory (GNOME session inhibitors still pending)
- [ ] Scheduled service discovery and conservative timer safety checks
- [x] One-shot read-only `check` verdict with reason codes (continuous MONITOR and audit log pending)
- [x] Explicit foreground MONITOR supervisor with persistent OFF shutdown and safe invalid-config failure
- [ ] Optional systemd user service, startup supervision, failure recovery and local minimal audit log
- [ ] Tests using synthetic system states; zero suspend actions

## Phase 2 — controlled power management

- [x] Persistent OFF/MONITOR user preference (no daemon or power effects)
- [ ] Safe ON operator control and enforced mode transitions in a persistent service
- [ ] Time-limited Keep Awake
- [ ] Safe inhibitor lifecycle and privileged suspend executor
- [ ] Reversible GNOME settings ownership, recovery and uninstall
- [ ] Race-safe pre-suspend check; opt-in only
- [ ] Suspend/resume integration tests on non-critical hardware

## Phase 3 — optional adapters

- [x] Pure vendor-independent lifecycle event interpreter (no transport or installed hooks)
- [x] Strict privacy-minimal hook payload parser and synthetic read-only CLI (no installation)
- [x] Ephemeral same-user Unix-socket event transport, aggregate event counters, and bounded foreground monitor (not authoritative coverage)
- [x] Opt-in temporary private socket discovery and non-blocking hook relay (vendor configurations untouched)
- [ ] Persistent authenticated/supervised adapter transport with integrity and loss detection
- [x] Read-only Claude Code and Codex hook integration plan (no config modifications)
- [x] Optional single-source append-only hook enable/disable with private backups, isolated tests (not yet applied on user host)
- [ ] Trusted agent-origin verification and authenticated process supervision
- [ ] OpenClaw, Ollama, build/test tools, transfer jobs, backup jobs
- [ ] Active children and external waits; no idle assumptions from CPU
- [ ] Optional desktop toggle

## Phase 4 — portability and releases

- [ ] Supported distro matrix, packaging, documentation, security review
- [ ] Hardware-specific sleep and wake regression tests
- [ ] Public release process with repeatable builds and reproducible evidence

No feature requiring privileged changes should be rushed into Phase 0.
