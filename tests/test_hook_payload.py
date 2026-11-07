import io
import json
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.hook_payload import (
    HookObservation, MAX_HOOK_INPUT_BYTES, SUPPORTED_SOURCES,
    parse_hook_payload, read_hook_payload,
)
from isc.policy import WorkState


def payload(**updates):
    data = {
        "session_id": "session_private_uuid_123",
        "hook_event_name": "PreToolUse",
        "transcript_path": "/private/sensitive/transcript",
        "cwd": "/private/project",
        "tool_input": {
            "command": "echo secret",
            "prompt": "MY SUPER SECRET PRIVATE PROMPT",
            "api_key": "MY PRIVATE TOKEN",
        },
        "tool_response": {"stdout": "secret_output"},
    }
    data.update(updates)
    return json.dumps(data).encode("utf-8")


class HookPayloadTests(unittest.TestCase):
    def test_claude_pretool_use_is_active(self):
        record = parse_hook_payload("claude_code", payload())
        self.assertIsInstance(record, HookObservation)
        self.assertEqual(record.state, WorkState.ACTIVE)
        self.assertEqual(record.event_name, "PreToolUse")
        self.assertFalse(record.authoritative)

    def test_codex_pretool_use_is_active(self):
        record = parse_hook_payload("codex", payload())
        self.assertEqual(record.state, WorkState.ACTIVE)

    def test_codex_stop_cannot_establish_human_wait(self):
        record = parse_hook_payload(
            "codex", payload(hook_event_name="Stop", stop_hook_active=False)
        )
        self.assertEqual(record.state, WorkState.UNKNOWN)

    def test_permission_request_cannot_establish_human_wait(self):
        for source in SUPPORTED_SOURCES:
            with self.subTest(source=source):
                record = parse_hook_payload(
                    source, payload(hook_event_name="PermissionRequest")
                )
                self.assertEqual(record.state, WorkState.UNKNOWN)

    def test_session_end_is_unknown_not_completed(self):
        record = parse_hook_payload(
            "claude_code", payload(hook_event_name="SessionEnd")
        )
        self.assertEqual(record.state, WorkState.UNKNOWN)

    def test_unknown_hook_event_is_unknown(self):
        record = parse_hook_payload(
            "codex", payload(hook_event_name="NewFutureEvent")
        )
        self.assertEqual(record.state, WorkState.UNKNOWN)

    def test_no_payload_secrets_in_record(self):
        record = parse_hook_payload("claude_code", payload())
        rendered = repr(record)
        for sensitive in (
            "session_private_uuid_123", "/private", "MY SUPER SECRET",
            "MY PRIVATE TOKEN", "secret_output",
        ):
            self.assertNotIn(sensitive, rendered)
        self.assertEqual(
            set(record.__dataclass_fields__),
            {"source", "event_name", "session_fingerprint", "state"},
        )
        self.assertEqual(len(record.session_fingerprint), 32)

    def test_fingerprint_stable_per_session_and_separate_per_source(self):
        a = parse_hook_payload("codex", payload())
        b = parse_hook_payload("codex", payload(hook_event_name="Stop"))
        c = parse_hook_payload("claude_code", payload())
        self.assertEqual(a.session_fingerprint, b.session_fingerprint)
        self.assertNotEqual(a.session_fingerprint, c.session_fingerprint)

    def test_missing_fields_unknown(self):
        for raw in (b"{}", b"[]", b"null", b"42", b"", b"unparseable"):
            with self.subTest(raw=raw):
                self.assertIsNone(parse_hook_payload("codex", raw))

    def test_invalid_session_id_unknown(self):
        for ident in ("", "a\nb", "\u0000", "a" * 600, 13, None):
            with self.subTest(ident=repr(ident)[:10]):
                self.assertIsNone(
                    parse_hook_payload("codex", payload(session_id=ident))
                )

    def test_invalid_event_name_unknown(self):
        for event in ("", "bad event", "a" * 70, "Bad/other", 5, None):
            with self.subTest(event=repr(event)[:10]):
                self.assertIsNone(
                    parse_hook_payload("codex", payload(hook_event_name=event))
                )

    def test_unknown_source_ignored(self):
        self.assertIsNone(parse_hook_payload("untrusted_source", payload()))

    def test_size_limit_fails_closed(self):
        large = payload(padding="x" * MAX_HOOK_INPUT_BYTES)
        self.assertIsNone(parse_hook_payload("codex", large))
        self.assertIsNone(read_hook_payload("codex", io.BytesIO(large)))

    def test_unreadable_stream_unknown(self):
        class BrokenStream:
            def read(self, size):
                raise OSError("cannot read")
        self.assertIsNone(read_hook_payload("codex", BrokenStream()))

    def test_read_from_stream(self):
        result = read_hook_payload("codex", io.BytesIO(payload()))
        self.assertEqual(result.state, WorkState.ACTIVE)


if __name__ == "__main__":
    unittest.main()
