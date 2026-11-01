# Roadmap

## Phase 0 — repository foundation (this branch)

- [x] Apache 2.0 licence (already present in repository)
- [x] Standalone architecture and safety criteria
- [x] Pure fail-closed eligibility policy with unit tests
- [x] Read-only `smart-sleep status` CLI
- [x] Linux CI without actual suspend
- [ ] Review and merge

## Phase 1 — observability

- [ ] Local work registry, event/lease contract, and TTL expiry -> UNKNOWN
- [ ] GNOME user-idle observation and logind inhibitor inventory
- [ ] Scheduled service discovery and conservative timer safety checks
- [ ] Monitor mode audit log with explanation of every decision
- [ ] Tests using synthetic system states; zero suspend actions

## Phase 2 — controlled power management

- [ ] Persistent ON / OFF / MONITOR modes
- [ ] Time-limited Keep Awake
- [ ] Safe inhibitor lifecycle and privileged suspend executor
- [ ] Reversible GNOME settings ownership, recovery and uninstall
- [ ] Race-safe pre-suspend check; opt-in only
- [ ] Suspend/resume integration tests on non-critical hardware

## Phase 3 — optional adapters

- [ ] Generic application event protocol; independent of any vendor
- [ ] Claude Code and Codex task lifecycle adapters
- [ ] OpenClaw, Ollama, build/test tools, transfer jobs, backup jobs
- [ ] Active children and external waits; no idle assumptions from CPU
- [ ] Optional desktop toggle

## Phase 4 — portability and releases

- [ ] Supported distro matrix, packaging, documentation, security review
- [ ] Hardware-specific sleep and wake regression tests
- [ ] Public release process with repeatable builds and reproducible evidence

No feature requiring privileged changes should be rushed into Phase 0.
