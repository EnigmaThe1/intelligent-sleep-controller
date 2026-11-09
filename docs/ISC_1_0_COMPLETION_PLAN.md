# ISC 1.0 — Four-milestone completion contract

**Scope frozen.** ISC is a small, independent, local-first Linux desktop
utility, not an event-observability platform. This document supersedes the
Step 99 experimental development sequence as the active delivery priority.
Existing experimental probes stay available for diagnostics only. No new
D-Bus protocol layer or monitoring subsystem is authorised unless a
concrete release-blocking defect demonstrates a need.

## Version 1.0 definition of done

ISC must provide a persistent **OFF / MONITOR / ON** control, an
easy-to-use tray/launcher and **Keep Awake**, with conservative,
explainable decisions. It must not interrupt active development work,
including detached child work and scheduled tasks. It must not depend
on Pandora, OpenClaw, credentials, tokens, hosted services or proprietary
agent infrastructure.

OFF: ISC never automatically requests sleep; it must also own or
safely neutralise conflicting GNOME automatic suspend settings without
overriding administrator policy, and restore changes on exit/uninstall.
MONITOR: full observation and reasons, no automatic power actions.
ON: requires a fresh, complete and trustworthy workload/wake assessment,
verified user inactivity, checked inhibitors, deliberate opt-in,
last-moment revalidation and a reliable hardware sleep/resume path.
Missing information always blocks sleep, never silently downgrades safety.

## The four delivery milestones

| Milestone | Required implementation | Exit test |
| --- | --- | --- |
| **1 — Consolidate** | Stabilise OFF/MONITOR, tray, Keep Awake; specify one shared mode state machine and compact diagnostic decision path; remove duplicate critical paths from release scope. | Mode persists across reboot; invalid settings fail to OFF; the app is usable without shell commands; no power action. |
| **2 — Safe automatic execution** | Add the native auto-suspend request behind an explicit ON mode gate, fresh systemd/logind/GNOME checks, current-user idle threshold, immediately repeated policy assessment, and cancellation on mode change. Initially ship **disarmed**, with ON unavailable until release gates pass. | Synthetic tests prove all missing, stale, active, inhibited, changed-mode and unknown states cannot invoke suspend. No real suspend in CI. |
| **3 — Minimum trusted work protection** | Reuse existing process/cgroup/invocation, inhibitor, scheduled-service and agent-hook evidence. Define a finite set of supported/unsupported workloads and a deterministic fail-closed unknown classification; do not introduce a monitoring platform. | Test active build/AI task, detached child, external wait, scheduled work, failed hooks and stale samples. Every unverified case blocks sleep. |
| **4 — Hardware verification & release** | Test suspend/resume, multi-monitor recovery, task persistence and controlled trial on non-critical hardware; verify installation, OFF enforcement, Keep Awake and clean uninstall/rollback. | Explicit operator approval for hardware trials; repeated reliable wake/recovery and no lost work. Release 1.0 only after all gates are green. |

## Current baseline (2026-11-09)

- Source branch: `feature/isc-foundation-monitor-only`, baseline
  `c006d3768a204048b630a659ee2b8337e3050f50`.
- 905/905 regression tests passed locally; `isc-monitor.service` active.
- OFF/MONITOR persistent selection, GNOME idle, manual suspend preview,
  Keep Awake and native inhibitors already exist.
- `settings.py` explicitly refuses ON; `supervisor.py` is MONITOR-only.
- `manual_power.py` has interactive native suspend with inhibitor checks.
  **Do not reuse its manual confirmation bypass for automatic execution.**
- `policy.py` defaults incomplete workload and scheduled-work evidence to
  unsafe; the latest live check still reports three unverified processes.
- Desktop suspend/resume has previously failed to restore displays
  reliably. **No unattended sleep on this host until that is resolved.**
- Experimental Step 97–99j D-Bus work is frozen, not deleted.

## Scope control and acceptance

A release milestone must deliver a user-observable outcome and a concise
test report, not another numbered isolated diagnostic. Fix release-blocking
bugs in the existing components. Keep tests relevant, without setting a
numeric test-count target. Do not remove existing user data, untracked
files, settings, active monitor service, or GNOME configuration as part
of development.

The four milestones are delivery categories, not a claim that automatic
suspend can safely be enabled after four commits. A failed acceptance gate
remains a blocker, not a reason to conceal the unknown. No suspend/wake
action is performed as part of continuous integration.

**Immediate next code task:** a single safe ON-mode eligibility contract,
initially disarmed, with tests for persistent OFF/MONITOR, mode changes,
unknown workloads, failed inhibitor checks and stale decision data.
