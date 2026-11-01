import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.inhibitors import parse_inhibitors, read_inhibitors


def entry(what="sleep", who="NetworkManager", why="cleanup devices", mode="delay",
          uid=0, pid=2413):
    return f'"{what}" "{who}" "{why}" "{mode}" {uid} {pid}'


class InhibitorParseTests(unittest.TestCase):
    def test_empty_list_is_verified_empty(self):
        self.assertEqual(parse_inhibitors("a(ssssuu) 0"), ())

    def test_sleep_delay_does_not_block(self):
        locks = parse_inhibitors("a(ssssuu) 1 " + entry())
        self.assertIsNotNone(locks)
        self.assertFalse(locks[0].blocks_automatic_sleep)
        self.assertTrue(locks[0].delays_sleep)

    def test_sleep_block_prevents_auto_sleep(self):
        locks = parse_inhibitors("a(ssssuu) 1 " + entry(mode="block"))
        self.assertTrue(locks[0].blocks_automatic_sleep)

    def test_idle_block_prevents_auto_sleep(self):
        locks = parse_inhibitors("a(ssssuu) 1 " + entry(what="idle", mode="block"))
        self.assertTrue(locks[0].blocks_automatic_sleep)

    def test_block_weak_is_conservatively_protected(self):
        locks = parse_inhibitors("a(ssssuu) 1 " + entry(mode="block-weak"))
        self.assertTrue(locks[0].blocks_automatic_sleep)

    def test_shutdown_only_is_not_a_sleep_blocker(self):
        locks = parse_inhibitors("a(ssssuu) 1 " + entry(what="shutdown", mode="block"))
        self.assertFalse(locks[0].blocks_automatic_sleep)

    def test_power_key_inhibitor_is_not_a_sleep_blocker(self):
        locks = parse_inhibitors("a(ssssuu) 1 " + entry(what="handle-power-key", mode="block"))
        self.assertFalse(locks[0].blocks_automatic_sleep)

    def test_unknown_operation_is_conservatively_blocking(self):
        locks = parse_inhibitors("a(ssssuu) 1 " + entry(what="new-systemd-operation", mode="block"))
        self.assertTrue(locks[0].blocks_automatic_sleep)

    def test_mixed_operations_and_quotes(self):
        raw = 'a(ssssuu) 2 ' + entry(what="sleep:idle", who="App \\"A\\"", mode="block") \
              + " " + entry(what="shutdown", mode="delay")
        locks = parse_inhibitors(raw)
        self.assertEqual(len(locks), 2)
        self.assertTrue(locks[0].blocks_automatic_sleep)
        self.assertFalse(locks[1].blocks_automatic_sleep)

    def test_malformed_replies_fail_closed(self):
        for raw in (
            "", "a(ssssuu)", "a(ssssuu) -1", "a(ssssuu) 1",
            "a(ssssuu) 9999999", "a(ssssuu) 0 trailing",
            "a(ssssuu) 1 " + entry(mode="unexpected"),
            "a(ssssuu) 1 " + entry(what="sleep:"),
            "a(ssssuu) 1 " + entry(uid="-1"),
            "a(ssssuu) 1 " + entry(pid=2**32),
            'a(ssssuu) 1 "unterminated',
            "a(ssssuu) 1 " + entry() + " unexpected",
        ):
            with self.subTest(raw=raw):
                self.assertIsNone(parse_inhibitors(raw))

    @patch("isc.inhibitors.subprocess.run")
    def test_busctl_command_is_read_only_no_shell(self, run):
        run.return_value = SimpleNamespace(returncode=0, stdout="a(ssssuu) 0")
        self.assertEqual(read_inhibitors(), ())
        args, kwargs = run.call_args
        self.assertEqual(args[0][0], "busctl")
        self.assertIn("ListInhibitors", args[0])
        self.assertEqual(kwargs["timeout"], 3.0)
        self.assertNotIn("shell", kwargs)

    @patch("isc.inhibitors.subprocess.run")
    def test_bus_failure_is_unknown(self, run):
        run.return_value = SimpleNamespace(returncode=1, stdout="a(ssssuu) 0")
        self.assertIsNone(read_inhibitors())

    @patch("isc.inhibitors.subprocess.run", side_effect=FileNotFoundError)
    def test_missing_busctl_is_unknown(self, run):
        self.assertIsNone(read_inhibitors())

    @patch("isc.inhibitors.subprocess.run", side_effect=subprocess.TimeoutExpired(["busctl"], 3))
    def test_timeout_is_unknown(self, run):
        self.assertIsNone(read_inhibitors())


if __name__ == "__main__":
    unittest.main()
