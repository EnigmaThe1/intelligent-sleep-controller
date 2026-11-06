# Safety and release gates

**Foundation status:** read-only. No automatic suspend path exists.

Before any automatic sleep capability is released:

1. Test the pure policy with active, external-wait, human-blocked, complete, unknown, and child-work states, including overlapping jobs.
2. Ensure missing/expired adapters, incomplete coverage, unknown idle duration, and pending timers all reject automatic suspension.
3. Demonstrate persistent ON / OFF / MONITOR and Keep Awake, plus clean restoration of previous GNOME settings after uninstall.
4. Validate existing logind/systemd inhibitors, user presence, and a second pre-suspend recheck; resist races between evaluation and suspend.
5. Test cancellation/rollback, crash recovery, system reboot, and suspend/resume failure.
6. Verify unattended scheduled work behaviour, especially missed timers and systems that cannot wake from RTC.
7. Validate NVIDIA/CUDA, open file handles, service reconnection, and representative long-running jobs on dedicated test hardware.
8. Never enable automatic suspend by default during installation or CI. First deploy monitor-only, with a clear opt-in.
9. Do not log prompt content, environment variables, process arguments, passwords, private repo paths, or tokens.
10. Keep compatibility tests separate from any user's active development work.

**Known limitations:** even an eligible pure-policy verdict is not permission to suspend. External/user activity detection and exhaustive coverage of arbitrary third-party applications remain unsolved until specific integrations and a final executor are implemented.

## Manual power safety gate

Manual sleep is only available behind an explicit interactive double confirmation, never from a monitoring hook or timer. The CLI preview does not submit a power request. Confirmed manual suspend checks Linux logind capability, inhibitor locks and the current user's process candidate inventory, and fails closed on missing probes. A limited process list is **not** evidence that background work is safely quiescent; the operator is warned of that limitation and may choose whether to proceed. It must not bypass logind inhibition or use `systemctl --force`/`-i`, and CI must mock all real suspend operations. Suspend/resume problems are hardware-specific; actual physical testing requires a saved working state and a recovery plan.

## Timed Keep Awake

Keep Awake is entirely opt-in. It prevents systemd-managed automatic idle
sleep and can block deliberate suspend requests while active. This is
disclosed in both the CLI and tray; cancel it before requesting manual
Sleep/Suspend. System shutdown is deliberately *not* inhibited. Finite presets are capped at 240 minutes.
The separately requested **Until Cancelled** mode has no timer and can
increase power consumption indefinitely while its owning process runs.
The GUI labels it explicitly; users can cancel at any time, and exiting
the tray or terminal releases its inhibitor. It is not silently restored
at the next login. The backend refuses to
claim success without verifying its own systemd/logind inhibitor and
releases the owned child on expiry, Ctrl+C or tray exit. Tests mock all
systemd-inhibit and process-signal operations; CI must never create an
actual inhibitor or attempt suspend. Keep Awake neither enables nor
bypasses automatic suspend eligibility checks.

## Native power actions and display-only sleep

ISC queries the host's own logind capability for Hibernate and Hybrid Sleep,
checks existing sleep inhibitors and known process candidates, and requires
an unmistakable manual confirmation before asking systemd to perform a
power operation. Capability alone does not guarantee that the system's
swap/resume configuration or GPU drivers will resume safely. Actual tests
must be performed deliberately with saved work and a recovery plan. ISC
never overrides systemd inhibitors or privileges.

Turning off monitor images is separate from system sleep. On Xorg, ISC
supports only a confirmed `xset dpms force off` operation after checking
that X11 DPMS is active. It does not change GNOME's global display idle
policy or suspend the computer. Wayland sessions must be reported as
unsupported unless a valid compositor-specific API is available.

Power-profile changes must go through the installed native
`powerprofilesctl` provider and only expose names actually supported by
the current machine. This is a deliberate user setting, not a sleep policy
or an inferred safe-to-suspend signal. Native GNOME Power settings should
be opened directly rather than replicated in ISC.

## Monitor-only native user service

A user-managed systemd service may be installed and enabled only through an
explicit confirmed operation. It must never install system-wide privileges,
overrule active inhibitors, alter power profiles, or trigger suspend.
OFF disables monitoring work even if the supervising process remains alive.
MONITOR accepts only privacy-limited metadata; reception does not establish
trusted coverage of independent workloads. Supervisor restarts must use
bounded backoff and must not overwrite an active or foreign runtime socket.
Crash recovery is limited to the application's own private, stale socket.

## Keep Awake owner-loss recovery

A native systemd inhibitor is held only while the owner's child worker is
alive. The worker watches its exclusive parent-owned stdin pipe, so an
unexpected parent death produces EOF and causes the worker to exit. Normal
cancellation also closes that pipe. There is no unrestricted process-group
kill. Tests run the worker without acquiring any real sleep inhibition.
Live OS integration requires separate user-controlled testing.

## Native scheduled-work discovery boundaries

Enumerating login sessions or currently loaded systemd timers is not
comprehensive scheduling coverage. Cron, at, user-space automation,
application-managed schedulers, remote requests and timers not loaded in
memory may require separate adapters. Missing queries must remain UNKNOWN.
No output from the read-only native observer may mark automatic sleep
eligible; the ON executor remains unavailable.

## Synthetic probes must not affect sleep readiness

A synthetic hook diagnostic is counted separately and must not create
or complete real work, refresh real-work timestamps, or establish scheduler
coverage. Event source names on the Unix socket are same-user claims, not
cryptographic proof of the originating application. The status view may
display only aggregated counts and must never use their absence as proof
of safe suspend eligibility.
