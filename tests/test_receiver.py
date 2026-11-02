import json
import os
from pathlib import Path
import socket
from tempfile import TemporaryDirectory
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.hook_payload import HookObservation, parse_hook_payload
from isc.policy import WorkState
from isc.receiver import (
    LocalMonitor, decode_observation, encode_observation, send_observation,
)


def observed(event="Stop", source="codex", session="private_session"):
    import json
    data = json.dumps({
        "session_id": session,
        "hook_event_name": event,
        "prompt": "SECRET_PROMPT",
        "tool_input": {"api_key": "SECRET_TOKEN"},
    }).encode()
    return parse_hook_payload(source, data)


class ProtocolTests(unittest.TestCase):
    def test_wire_does_not_contain_private_text(self):
        wire = encode_observation(observed())
        self.assertNotIn(b"SECRET", wire)
        self.assertNotIn(b"private_session", wire)
        self.assertEqual(set(json.loads(wire)), {
            "source", "event_name", "session_fingerprint"
        })

    def test_does_not_accept_state_override(self):
        bad = json.loads(encode_observation(observed()))
        bad["state"] = "completed"
        self.assertIsNone(decode_observation(json.dumps(bad).encode()))

    def test_rejects_unknown_source(self):
        bad = json.loads(encode_observation(observed()))
        bad["source"] = "unknown"
        self.assertIsNone(decode_observation(json.dumps(bad).encode()))

    def test_rejects_oversized_and_invalid_json(self):
        for raw in (b"x" * 1025, b"garbage", b"[]", b"{}"):
            self.assertIsNone(decode_observation(raw))

    def test_rejects_forged_fingerprint(self):
        bad = json.loads(encode_observation(observed()))
        bad["session_fingerprint"] = "../private_secret"
        self.assertIsNone(decode_observation(json.dumps(bad).encode()))

    def test_missing_receiver_fails_open_for_sender(self):
        self.assertFalse(send_observation("/no/such/socket", observed()))

    def test_invalid_observation_rejected(self):
        with self.assertRaises(ValueError):
            encode_observation(HookObservation(
                "codex", "Bad/Event", "f" * 32, WorkState.UNKNOWN,
            ))


class ReceiverTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def test_creates_private_socket_and_cleans_afterwards(self):
        with LocalMonitor(runtime_dir=self.base) as server:
            path = server.path
            self.assertTrue(path.exists())
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertFalse(path.exists())
        self.assertFalse(path.parent.exists())

    def test_accepts_stop_but_never_confirms_completion(self):
        with LocalMonitor(runtime_dir=self.base) as server:
            self.assertTrue(send_observation(server.path, observed("Stop")))
            server.run_for(0.05)
            self.assertEqual(server.accepted, 1)
            self.assertEqual(server.counts["unknown"], 1)
            item, = server.registry.observed_work(now=__import__("time").monotonic())
            self.assertEqual(item.state, WorkState.UNKNOWN)

    def test_active_and_stop_events_are_separate(self):
        with LocalMonitor(runtime_dir=self.base) as server:
            self.assertTrue(send_observation(server.path, observed("PreToolUse")))
            self.assertTrue(send_observation(server.path, observed("Stop", session="other")))
            server.run_for(0.05)
            self.assertEqual(server.accepted, 2)
            self.assertEqual(server.counts["active"], 1)
            self.assertEqual(server.counts["unknown"], 1)
            self.assertEqual(server.registry.count(), 2)

    def test_bad_payload_rejected(self):
        with LocalMonitor(runtime_dir=self.base) as server:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
                peer.connect(str(server.path))
                peer.sendall(b'{"tool_input":"secret"}')
            server.run_for(0.05)
            self.assertEqual(server.rejected, 1)
            self.assertEqual(server.registry.count(), 0)

    def test_no_socket_if_runtime_directory_not_private(self):
        if os.getuid() == 0:
            self.skipTest("root bypasses permission checks, verify mode separately")
        self.base.chmod(0o755)
        with self.assertRaises(PermissionError):
            LocalMonitor(runtime_dir=self.base)

    def test_non_linux_missing_peercred_fails_closed(self):
        with LocalMonitor(runtime_dir=self.base) as server:
            self.assertTrue(send_observation(server.path, observed()))
            with patch.object(socket, "SO_PEERCRED", None):
                # An unsupported peercred mode cannot be assumed safe.
                server.run_for(0.05)
            self.assertEqual(server.accepted, 0)
            self.assertEqual(server.rejected, 1)

    def test_zero_duration_disallowed(self):
        with LocalMonitor(runtime_dir=self.base) as server:
            with self.assertRaises(ValueError):
                server.run_for(0)

    def test_receiver_never_touches_external_files(self):
        unrelated = self.base / "unrelated.txt"
        unrelated.write_text("leave me alone")
        with LocalMonitor(runtime_dir=self.base) as server:
            self.assertTrue(send_observation(server.path, observed()))
            server.run_for(0.05)
        self.assertEqual(unrelated.read_text(), "leave me alone")


if __name__ == "__main__":
    unittest.main()
