import os
from pathlib import Path
import signal
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.keepawake import (
    INHIBITOR_EXECUTABLE, KeepAwakeError,
    KeepAwakeManager, _observed_lock, inhibitor_command,
)


class KeepAwakeTests(unittest.TestCase):
    def test_command_inhibits_only_idle_and_sleep(self):
        cmd = inhibitor_command(30, "abc123")
        self.assertEqual(cmd[0], "/usr/bin/systemd-inhibit")
        self.assertIn("--what=idle:sleep", cmd)
        self.assertIn("--mode=block", cmd)
        self.assertEqual(cmd[-5:], (sys.executable, "-B", "-m", "isc.keepawake_worker", "1800"))
        self.assertNotIn("--what=shutdown", cmd)
        self.assertNotIn("systemctl", cmd)
        self.assertNotIn("sudo", cmd)

    def test_unexpected_duration_cannot_start(self):
        for val in (0, -1, 10, 1440, 30.5, "30", True):
            with self.subTest(val=val):
                with self.assertRaises(KeepAwakeError):
                    inhibitor_command(val, "abc123")

    def test_unsafe_marker_rejected(self):
        for val in ("", "thing with spaces", "../x", "a;b"):
            with self.subTest(val=val):
                with self.assertRaises(KeepAwakeError):
                    inhibitor_command(15, val)

    @patch("isc.keepawake.subprocess.run")
    def test_observed_lock_requires_exact_identity_and_tags(self, run):
        def reply(why, what="idle:sleep", who="Intelligent Sleep Controller", mode="block"):
            return (
                'a(ssssuu) 1 "' + what + '" "' + who + '" "'
                + why + '" "' + mode + '" ' + str(os.getuid()) + " 1001"
            )
        run.return_value = SimpleNamespace(
            returncode=0, stdout=reply("ISC timed Keep Awake abc123"),
        )
        self.assertTrue(_observed_lock("abc123"))
        run.return_value = SimpleNamespace(
            returncode=0, stdout=reply("ISC timed Keep Awake someoneelse"),
        )
        self.assertFalse(_observed_lock("abc123"))
        run.return_value = SimpleNamespace(
            returncode=0, stdout=reply("ISC timed Keep Awake abc123", mode="delay"),
        )
        self.assertFalse(_observed_lock("abc123"))
        run.return_value = SimpleNamespace(
            returncode=0, stdout=reply("ISC timed Keep Awake abc123", what="sleep"),
        )
        self.assertFalse(_observed_lock("abc123"))

    @patch("isc.keepawake.subprocess.run")
    def test_unknown_inhibitor_inventory_is_not_verified(self, run):
        run.return_value = SimpleNamespace(returncode=0, stdout="not valid")
        self.assertFalse(_observed_lock("abc"))
        run.return_value = SimpleNamespace(returncode=1, stdout="a(ssssuu) 0")
        self.assertFalse(_observed_lock("abc"))

    def _working_manager(self, *, observed=True):
        manager = KeepAwakeManager()
        child = Mock()
        child.poll.return_value = None
        child.pid = 4321
        return manager, child

    @patch("isc.keepawake.os.killpg")
    @patch("isc.keepawake.os.getpgid", return_value=4321)
    @patch("isc.keepawake._observed_lock", return_value=True)
    @patch("isc.keepawake.os.access", return_value=True)
    @patch("isc.keepawake.subprocess.Popen")
    def test_start_is_explicit_and_verifies_lock(
        self, spawn, executable, observed, getpgid, killpg,
    ):
        manager, child = self._working_manager()
        spawn.return_value = child
        try:
            status = manager.start(15)
            self.assertTrue(status.requested)
            self.assertGreater(status.remaining_seconds, 0)
            self.assertLessEqual(status.remaining_seconds, 900)
            call = spawn.call_args
            self.assertEqual(call.args[0][0], INHIBITOR_EXECUTABLE)
            self.assertEqual(call.args[0][-4:-1], ("-B", "-m", "isc.keepawake_worker"))
            self.assertEqual(call.kwargs["stdin"], subprocess.PIPE)
            self.assertTrue(call.kwargs["start_new_session"])
            self.assertTrue(call.kwargs["close_fds"])
            observed.assert_called()
        finally:
            manager.cancel()
        killpg.assert_not_called()

    @patch("isc.keepawake.os.killpg")
    @patch("isc.keepawake.os.getpgid", return_value=4321)
    @patch("isc.keepawake._observed_lock", return_value=True)
    @patch("isc.keepawake.os.access", return_value=True)
    @patch("isc.keepawake.subprocess.Popen")
    @patch.dict(os.environ, {"AGENT_SECRET_TOKEN": "PRIVATE_TOKEN_VALUE"})
    def test_child_does_not_inherit_unrelated_secrets(
        self, spawn, executable, observed, getpgid, killpg,
    ):
        manager, child = self._working_manager()
        spawn.return_value = child
        try:
            manager.start(30)
            env = spawn.call_args.kwargs["env"]
            self.assertNotIn("AGENT_SECRET_TOKEN", env)
            self.assertNotIn("PRIVATE_TOKEN_VALUE", repr(env))
        finally:
            manager.cancel()

    @patch("isc.keepawake.os.killpg")
    @patch("isc.keepawake.os.getpgid", return_value=4321)
    @patch("isc.keepawake.time.sleep")
    @patch("isc.keepawake._observed_lock", return_value=False)
    @patch("isc.keepawake.os.access", return_value=True)
    @patch("isc.keepawake.subprocess.Popen")
    def test_no_verified_inhibitor_means_no_claimed_success(
        self, spawn, executable, observed, sleep, getpgid, killpg,
    ):
        manager, child = self._working_manager()
        spawn.return_value = child
        with patch("isc.keepawake.time.monotonic", side_effect=[
            100.0, 100.0, 100.1, 102.6,
        ]):
            with self.assertRaises(KeepAwakeError):
                manager.start(15)
        killpg.assert_called_once_with(4321, signal.SIGTERM)
        self.assertFalse(manager.status().requested)

    @patch("isc.keepawake.os.access", return_value=False)
    @patch("isc.keepawake.subprocess.Popen")
    def test_missing_systemd_inhibit_does_not_start(self, spawn, access):
        with self.assertRaises(KeepAwakeError):
            KeepAwakeManager().start(15)
        spawn.assert_not_called()

    @patch("isc.keepawake.os.access", return_value=True)
    @patch("isc.keepawake.subprocess.Popen", side_effect=OSError("private"))
    def test_spawn_failure_has_no_private_error_details(self, spawn, access):
        with self.assertRaisesRegex(KeepAwakeError, "inhibitor_start_failed"):
            KeepAwakeManager().start(15)

    def test_cancel_without_start_is_safe_noop(self):
        guard = KeepAwakeManager()
        guard.cancel()
        self.assertFalse(guard.status().requested)

    @patch("isc.keepawake.os.killpg")
    @patch("isc.keepawake.os.getpgid", return_value=4321)
    @patch("isc.keepawake._observed_lock", return_value=True)
    @patch("isc.keepawake.os.access", return_value=True)
    @patch("isc.keepawake.subprocess.Popen")
    def test_cancel_idempotent(self, spawn, access, observed, getpgid, killpg):
        manager, child = self._working_manager()
        spawn.return_value = child
        manager.start(15)
        manager.cancel()
        manager.cancel()
        self.assertEqual(killpg.call_count, 0)
        self.assertFalse(manager.status().requested)

    @patch("isc.keepawake.os.killpg", side_effect=AssertionError("other group signalled"))
    @patch("isc.keepawake.os.getpgid", return_value=9999)
    @patch("isc.keepawake._observed_lock", return_value=True)
    @patch("isc.keepawake.os.access", return_value=True)
    @patch("isc.keepawake.subprocess.Popen")
    def test_cannot_signal_unowned_process_group(
        self, spawn, access, observed, getpgid, killpg,
    ):
        manager, child = self._working_manager()
        spawn.return_value = child
        manager.start(15)
        child.wait.side_effect = subprocess.TimeoutExpired("inhibit", 3)
        with self.assertRaisesRegex(
            KeepAwakeError, "inhibitor_process_identity_changed",
        ):
            manager.cancel()
        killpg.assert_not_called()


    def test_infinite_command_uses_native_coreutils_sleep(self):
        args = inhibitor_command(None, "abc123")
        self.assertEqual(args[-4:], ("-B", "-m", "isc.keepawake_worker", "forever"))
        self.assertIn("--what=idle:sleep", args)

    @patch("isc.keepawake.os.killpg")
    @patch("isc.keepawake.os.getpgid", return_value=4321)
    @patch("isc.keepawake._observed_lock", return_value=True)
    @patch("isc.keepawake.os.access", return_value=True)
    @patch("isc.keepawake.subprocess.Popen")
    def test_indefinite_inhibitor_reports_no_expiry_and_can_cancel(
        self, spawn, executable, observed, getpgid, killpg,
    ):
        manager, child = self._working_manager()
        spawn.return_value = child
        status = manager.start(None)
        self.assertTrue(status.requested)
        self.assertIsNone(status.remaining_seconds)
        self.assertEqual(spawn.call_args.args[0][-1], "forever")
        self.assertIsNone(manager.status().remaining_seconds)
        manager.cancel()
        killpg.assert_called_once_with(4321, signal.SIGTERM)


    @patch("isc.keepawake.os.killpg")
    @patch("isc.keepawake.os.getpgid", return_value=4321)
    @patch("isc.keepawake._observed_lock", return_value=True)
    @patch("isc.keepawake.os.access", return_value=True)
    @patch("isc.keepawake.subprocess.Popen")
    def test_cancel_prefers_pipe_eof_before_signals(
        self, spawn, executable, observed, getpgid, killpg,
    ):
        manager, child = self._working_manager()
        child.stdin.closed = False
        spawn.return_value = child
        manager.start(None)
        manager.cancel()
        child.stdin.close.assert_called_once()
        child.wait.assert_called_once_with(timeout=3)
        getpgid.assert_not_called()
        killpg.assert_not_called()

    @patch("isc.keepawake.os.killpg")
    @patch("isc.keepawake.os.getpgid", return_value=4321)
    @patch("isc.keepawake._observed_lock", return_value=True)
    @patch("isc.keepawake.os.access", return_value=True)
    @patch("isc.keepawake.subprocess.Popen")
    def test_cancel_fallback_only_targets_owned_child(
        self, spawn, executable, observed, getpgid, killpg,
    ):
        manager, child = self._working_manager()
        child.stdin.closed = False
        child.wait.side_effect = [
            subprocess.TimeoutExpired("inhibit", 3), 0,
        ]
        spawn.return_value = child
        manager.start(15)
        manager.cancel()
        child.stdin.close.assert_called_once()
        killpg.assert_called_once_with(4321, signal.SIGTERM)
        self.assertFalse(manager.status().requested)



if __name__ == "__main__":
    unittest.main()
