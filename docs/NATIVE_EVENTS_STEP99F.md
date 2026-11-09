# Step 99f — systemd D-Bus unique sender-owner correlation

The systemd 255 one-shot event observer from Step 99e returned
`QUIET_UNVERIFIED` on the Ubuntu desktop. Silence is expected and must
not be interpreted as complete workload observation.

This step introduces `native_systemd_sender.py`, a **separate opt-in**
read-only owner-query and pure correlation component. It uses a fixed,
four-second `busctl --user call org.freedesktop.DBus ... GetNameOwner`
query, restricted to the well-known `org.freedesktop.systemd1` name.
Only strictly formatted unique names are accepted. Missing, changed or
malformed identities remain non-authoritative.

A candidate unique sender matching the manager's name owner in two
bracketing samples yields `SENDER_MATCH_UNVERIFIED`; different owners
yield `OWNER_CHANGED_UNVERIFIED`; a mismatched candidate yields
`SENDER_MISMATCH`. All results leave delivery, owner authentication
and workload coverage flags false. The module stores no user-provided
service names, environment secrets or message payloads and does not
log unique sender identifiers.

**Not yet integrated:** The Step 99e live busctl observation does not
extract and return a candidate unique sender, nor does it bracket event
capture with owner reads. This step therefore does not claim live
sender verification. Integrating the two safely requires startup
ordering, careful unique-name matching, owner-change handling,
interrupted-stream handling and a transport whose message framing
can be trusted without consuming unbounded message payloads.

No running services or system configuration are modified. Automatic
suspend is not implemented; ISC remains MONITOR-only.
