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

    def test_event_counters_are_only_validated_source_and_type(self):
        with LocalMonitor(runtime_dir=self.base) as server:
            self.assertTrue(send_observation(
                server.path, observed("SessionStart", session="SECRET_SESSION_1"),
            ))
            self.assertTrue(send_observation(
                server.path, observed("Stop", session="SECRET_SESSION_2"),
            ))
            server.run_for(0.05)
            self.assertEqual(server.accepted, 2)
            self.assertEqual(server.event_counts[("codex", "SessionStart")], 1)
            self.assertEqual(server.event_counts[("codex", "Stop")], 1)
            self.assertNotIn("SECRET_SESSION", repr(server.event_counts))
            self.assertEqual(len(server.event_counts), 2)

    def test_subagent_start_and_stop_do_not_erase_unverified_child_work(self):
        from isc.policy import WorkState
        with LocalMonitor(runtime_dir=self.base) as monitor:
            session = "same-parent-task"
            events = ["UserPromptSubmit", "SubagentStart", "SubagentStop", "PostToolUse"]
            for event in events:
                self.assertTrue(send_observation(
                    monitor.path,
                    observed(event, source="claude_code", session=session),
                ))
                monitor.run_for(0.01)
            self.assertEqual(monitor.accepted, len(events))
            work = monitor.registry.observed_work(now=__import__("time").monotonic())
            self.assertEqual(len(work), 1)
            self.assertEqual(work[0].state, WorkState.ACTIVE)
            self.assertTrue(work[0].active_children)
            self.assertFalse(monitor.registry.tracking_history_lost)

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


    def test_advertised_socket_is_discoverable_and_removed(self):
        from isc.receiver import (
            MONITOR_DIRECTORY_NAME, discover_monitor_socket,
            send_discovered_observation,
        )
        self.assertIsNone(discover_monitor_socket(runtime_dir=self.base))
        with LocalMonitor(runtime_dir=self.base, advertise=True) as server:
            self.assertEqual(
                discover_monitor_socket(runtime_dir=self.base), server.path,
            )
            self.assertTrue(send_discovered_observation(
                observed("Stop"), runtime_dir=self.base,
            ))
            server.run_for(0.05)
            self.assertEqual(server.accepted, 1)
            self.assertEqual(server.counts["unknown"], 1)
        self.assertIsNone(discover_monitor_socket(runtime_dir=self.base))
        self.assertFalse((self.base / MONITOR_DIRECTORY_NAME).exists())

    def test_no_receiver_does_not_block_hook_sender(self):
        from isc.receiver import send_discovered_observation
        self.assertFalse(send_discovered_observation(
            observed(), runtime_dir=self.base,
        ))

    def test_second_advertised_monitor_does_not_clobber_first(self):
        with LocalMonitor(runtime_dir=self.base, advertise=True) as server:
            original = server.path.lstat().st_ino
            with self.assertRaises(OSError):
                LocalMonitor(runtime_dir=self.base, advertise=True)
            self.assertEqual(server.path.lstat().st_ino, original)

    def test_does_not_trust_advertised_symlink(self):
        from isc.receiver import discover_monitor_socket
        with LocalMonitor(runtime_dir=self.base) as server:
            alias = self.base / "isc-monitor"
            alias.symlink_to(server.path.parent, target_is_directory=True)
            self.assertIsNone(discover_monitor_socket(runtime_dir=self.base))
            alias.unlink()

    def test_discovery_rejects_socket_symlink(self):
        from isc.receiver import discover_monitor_socket
        with LocalMonitor(runtime_dir=self.base) as server:
            named = self.base / "isc-monitor"
            named.mkdir(mode=0o700)
            (named / "events.sock").symlink_to(server.path)
            self.assertIsNone(discover_monitor_socket(runtime_dir=self.base))

    def test_stale_directory_is_preserved_not_deleted(self):
        named = self.base / "isc-monitor"
        named.mkdir(mode=0o700)
        marker = named / "existing-data"
        marker.write_text("do not touch")
        with self.assertRaises(OSError):
            LocalMonitor(runtime_dir=self.base, advertise=True)
        self.assertEqual(marker.read_text(), "do not touch")

    def test_recovers_private_stale_directory_after_abrupt_exit(self):
        """A no-longer-listening socket is safely reclaimed before binding."""
        named = self.base / "isc-monitor"
        named.mkdir(mode=0o700)
        abandoned = named / "events.sock"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as old:
            old.bind(str(abandoned))
            abandoned.chmod(0o600)
        # The original socket is no longer listening, but its path remains.
        self.assertTrue(abandoned.exists())
        with LocalMonitor(runtime_dir=self.base, advertise=True) as fresh:
            self.assertEqual(fresh.path, abandoned)
            self.assertNotEqual(fresh._socket_inode, 0)
        self.assertFalse(named.exists())

    def test_recovers_empty_private_directory_after_crash(self):
        named = self.base / "isc-monitor"
        named.mkdir(mode=0o700)
        with LocalMonitor(runtime_dir=self.base, advertise=True):
            pass
        self.assertFalse(named.exists())

    def test_preserves_actively_listening_foreign_socket(self):
        named = self.base / "isc-monitor"
        named.mkdir(mode=0o700)
        socket_path = named / "events.sock"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as other:
            other.bind(str(socket_path))
            other.listen(1)
            socket_path.chmod(0o600)
            original_inode = socket_path.stat().st_ino
            with self.assertRaises(OSError):
                LocalMonitor(runtime_dir=self.base, advertise=True)
            self.assertEqual(socket_path.stat().st_ino, original_inode)
        socket_path.unlink()
        named.rmdir()

    def test_refuses_ambiguous_socket_symlink_without_deleting_target(self):
        named = self.base / "isc-monitor"
        named.mkdir(mode=0o700)
        destination = self.base / "secret_file"
        destination.write_text("keep")
        (named / "events.sock").symlink_to(destination)
        with self.assertRaises((OSError, PermissionError)):
            LocalMonitor(runtime_dir=self.base, advertise=True)
        self.assertEqual(destination.read_text(), "keep")
        (named / "events.sock").unlink()
        named.rmdir()

    def test_runtime_root_symlink_rejected(self):
        from isc.receiver import discover_monitor_socket
        alias = self.base.parent / (self.base.name + "-link")
        alias.symlink_to(self.base, target_is_directory=True)
        try:
            self.assertIsNone(discover_monitor_socket(runtime_dir=alias))
            with self.assertRaises(PermissionError):
                LocalMonitor(runtime_dir=alias, advertise=True)
        finally:
            alias.unlink()


    def test_unrecognised_event_names_share_single_bounded_bucket(self):
        with LocalMonitor(runtime_dir=self.base) as server:
            for n in range(40):
                self.assertTrue(send_observation(
                    server.path, observed(f"PrivateVariant{n}", session=f"example-{n}")
                ))
                server.run_for(0.002)
            self.assertEqual(server.accepted, 40)
            self.assertEqual(server.event_counts[("codex", "unrecognised")], 40)
            self.assertEqual(len(server.event_counts), 1)

    def test_slow_client_cannot_monopolise_receiver_indefinitely(self):
        from isc.receiver import MAX_CONNECTION_SECONDS
        import threading
        import time
        with LocalMonitor(runtime_dir=self.base) as server:
            slow = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            slow.connect(str(server.path))
            slow.sendall(b" ")
            start = time.monotonic()
            try:
                server.run_for(MAX_CONNECTION_SECONDS + 0.35)
            finally:
                slow.close()
            self.assertLess(time.monotonic() - start, MAX_CONNECTION_SECONDS + 0.7)
            self.assertEqual(server.accepted, 0)
            self.assertGreaterEqual(server.rejected, 1)



if __name__ == "__main__":
    unittest.main()
