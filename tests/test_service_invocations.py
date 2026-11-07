"""Native systemd activation identity, purely read-only and aggregate-only."""
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.service_invocations import (
    InvocationSnapshot, compare_service_invocations,
    read_service_invocations, _query,
)
from isc.cli import service_invocations_cli

A = "a" * 32
B = "b" * 32


class ServiceInvocationTests(unittest.TestCase):
    def test_same_cgroup_service_new_invocation_detectable(self):
        first = InvocationSnapshot((("job.service", A),))
        second = InvocationSnapshot((("job.service", B),))
        result = compare_service_invocations(first, second)
        self.assertEqual(result.changed, 1)
        self.assertEqual(result.retained, 0)
        self.assertFalse(result.coverage_verified)
        self.assertNotIn("job.service", repr(result))
        self.assertNotIn(A, repr(result))

    def test_equal_invocation_does_not_claim_task_finished(self):
        result = compare_service_invocations(
            InvocationSnapshot((("job.service", A),)),
            InvocationSnapshot((("job.service", A.upper()),)),
        )
        self.assertEqual(result.retained, 1)
        self.assertEqual(result.changed, 0)
        self.assertFalse(result.coverage_verified)

    def test_missing_invocations_are_unknown_not_stable(self):
        result = compare_service_invocations(
            InvocationSnapshot((("job.service", None),)),
            InvocationSnapshot((("job.service", A),)),
        )
        self.assertEqual(result.unknown, 1)
        self.assertEqual((result.retained, result.changed), (0, 0))

    def test_invalid_duplicate_or_missing_snapshots(self):
        first = InvocationSnapshot((("job.service", A),))
        for bad in (
            None, InvocationSnapshot((("job.timer", A),)),
            InvocationSnapshot((("job.service", "xyz"),)),
            InvocationSnapshot((("job.service", A), ("job.service", B))),
        ):
            self.assertIsNone(compare_service_invocations(first, bad))

    def test_read_only_queries_collect_private_ids_only_transiently(self):
        with patch("isc.service_invocations._capture", side_effect=[
            "a.timer loaded active waiting Test\n",
            "Id=a.timer\nUnit=linked.service\n",
            "Id=linked.service\nInvocationID=" + A + "\n",
            "b.timer loaded active waiting Test\n",
            "Id=b.timer\nUnit=other.service\n",
            "Id=other.service\nInvocationID=\n",
        ]) as capture:
            system, user = read_service_invocations()
        self.assertEqual(system.observed, (("linked.service", A),))
        self.assertEqual(user.observed, (("other.service", None),))
        self.assertEqual(capture.call_count, 6)
        self.assertEqual(
            capture.call_args_list[2].args[0],
            _query("--system", ("linked.service",)),
        )
        self.assertTrue(all(
            call.args[0][0] == "systemctl" and
            ("list-units" in call.args[0] or "show" in call.args[0])
            for call in capture.call_args_list
        ))

    def test_missing_or_malformed_systemd_response_unknown(self):
        with patch("isc.service_invocations._capture", side_effect=[
            "a.timer loaded active waiting Test\n",
            "Id=a.timer\nUnit=a.service\n",
            "Id=a.service\nInvocationID=bad\n",
            "",
        ]):
            system, user = read_service_invocations()
        self.assertIsNone(system)
        self.assertEqual(user.observed, ())

    @patch("isc.cli.time.sleep")
    @patch("isc.cli.read_service_invocations")
    def test_cli_avoids_private_service_identity(self, scanner, sleeper):
        scanner.side_effect = [
            (InvocationSnapshot((("private.service", A),)), InvocationSnapshot(())),
            (InvocationSnapshot((("private.service", B),)), InvocationSnapshot(())),
        ]
        from io import StringIO
        from contextlib import redirect_stdout
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(service_invocations_cli(seconds=1), 0)
        text = output.getvalue()
        self.assertIn("changed invocation=1", text)
        self.assertNotIn("private.service", text)
        self.assertNotIn(A, text)
        sleeper.assert_called_once_with(1)
        self.assertEqual(service_invocations_cli(seconds=61), 2)


if __name__ == "__main__":
    unittest.main()
