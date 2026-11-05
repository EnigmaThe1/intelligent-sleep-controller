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
Sleep/Suspend. System shutdown is deliberately *not* inhibited. A maximum
duration of 240 minutes prevents indefinite locks. The backend refuses to
claim success without verifying its own systemd/logind inhibitor and
releases the owned child on expiry, Ctrl+C or tray exit. Tests mock all
systemd-inhibit and process-signal operations; CI must never create an
actual inhibitor or attempt suspend. Keep Awake neither enables nor
bypasses automatic suspend eligibility checks.
