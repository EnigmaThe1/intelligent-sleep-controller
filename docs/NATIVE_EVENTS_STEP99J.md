# Step 99j — Harden live text-header recognition against payload spoofing

The locally verified Step 99i baseline passed 899 tests, and the live
two-window check returned WINDOWS_UNVERIFIED as expected. The optional
systemd 255 live observer previously searched the entire accumulated
`busctl monitor` text for a plausible Manager header. This allowed text
inside a prior message payload to resemble a later real header.

Step 99j now **only recognises the first complete header** at the start
of the observed stream, optionally immediately after the exact standard
`Monitoring bus message stream.` preamble. Unexpected leading text,
a different first message, a partial header or a header-looking string
inside a payload cannot be upgraded into an observed candidate.

This intentionally reduces diagnostic detection: a filtered stream with
unanticipated output becomes QUIET_UNVERIFIED or UNKNOWN instead of
attempting to scan forward to another event. That is safer than a
false sender correlation. The 16 KiB bound, 1–10 second limit, user-bus
scope, privacy-minimal results and process cleanup remain unchanged.
No persistent listener, service changes or automatic suspension are
introduced.

**Limitations:** A text parser cannot authenticate the D-Bus sender or
establish reliable delivery, frame boundaries, backpressure resilience
or whole-system task coverage. The matched first header is still
CANDIDATE_SIGNAL_UNVERIFIED; unique-owner bracketing still remains
non-atomic and non-authoritative. Before persistent monitoring, switch
to a protocol-aware native D-Bus transport with explicit connection,
subscription acknowledgment, loss detection and bounded metadata only.

The six additional regression tests include simulated payloads that
imitate complete signal headers and tests for unexpected prefixes or
prior unrelated messages. ISC remains MONITOR-only.
