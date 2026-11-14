# Step 99d — candidate systemd event header safety checks

ISC's systemd 255 transport experiment has been verified on the target
Ubuntu machine, but it currently discards its entire event stream.
The next step towards genuine delivery verification must not infer events
from process lifetime or silence.

This increment adds `native_signal_headers.py`: a pure bounded assessment
of pre-extracted Manager signal metadata. It recognises JobNew, JobRemoved,
UnitNew and UnitRemoved; rejects other types, interfaces, fields, malformed
values, or batches larger than 32; and returns only aggregate diagnostic
states. No names, event bodies, user data or identifiers are retained.

Important: `CANDIDATE_UNVERIFIED` is deliberately NOT evidence that a live
D-Bus event arrived. The input could be synthetic or copied. A future
transport must implement tested message framing, a source-owner check,
a connection-start handshake, loss detection, queue bounds and controlled
termination. None of these are implemented in this step.

No running services, socket listeners, permissions, power settings or
automatic suspend policies are changed. ISC remains MONITOR-only.
