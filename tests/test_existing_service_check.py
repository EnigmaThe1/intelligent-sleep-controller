"""Existing service observations are read-only and never grant sleep approval."""
from contextlib import redirect_stdout
from dataclasses import replace
from io import StringIO
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from isc.cgroup_subtree import CgroupSubtreeSummary
from isc.existing_service_check import (
    ExistingServiceObservation, observe_existing_user_service,
)
from isc.native_service_enrollment import NativeServiceProcess
from isc.process_lifecycle import ProcessIdentity
from isc.cli import main

NAME = "example-worker.service"
IDENTITY = ProcessIdentity(812, 12345, 1, "python3")
CGROUP = "/user.slice/user-1000.slice/user@1000.service/app.slice/example-worker.service"
EVIDENCE = NativeServiceProcess(
    IDENTITY, NAME, "--user", "a" * 32, CGROUP,
)


class ExistingServiceCheckTests(unittest.TestCase):
    def inspect(self, *, first=EVIDENCE, subtree=None, last=EVIDENCE):
        if subtree is None:
            subtree = CgroupSubtreeSummary(3, unknown=False)
        discovery = Mock(side_effect=(first, last))
        scan = Mock(return_value=subtree)
        result = observe_existing_user_service(
            NAME, discover=discovery, scan=scan,
        )
        return result, discovery, scan

    def test_stable_user_service_observed_without_claiming_application_idle(self):
        row, discover, scan = self.inspect()
        self.assertEqual(row, ExistingServiceObservation(
            observed_processes=3,
            reason="service_process_membership_observed_task_state_unknown",
            verified_service_membership=True,
        ))
        self.assertFalse(row.task_activity_verified)
        self.assertFalse(row.workload_coverage_complete)
        self.assertEqual(discover.call_count, 2)
        discover.assert_any_call(NAME, scope="--user")
        scan.assert_called_once_with(CGROUP)
        self.assertNotIn(NAME, repr(row))
        self.assertNotIn(str(IDENTITY.pid), repr(row))
        self.assertNotIn("python3", repr(row))
        self.assertNotIn(CGROUP, repr(row))

    def test_missing_or_unverifiable_native_evidence_is_unknown(self):
        for first in (
            None,
            "not-evidence",
            replace(EVIDENCE, scope="--system"),
            replace(EVIDENCE, service="other.service"),
            replace(EVIDENCE, coverage_verified=True),
            replace(EVIDENCE, task_owner_authenticated=True),
            replace(EVIDENCE, identity=replace(IDENTITY, start_ticks=0)),
            replace(EVIDENCE, identity=replace(IDENTITY, pid=0)),
            replace(EVIDENCE, invocation=""),
            replace(EVIDENCE, cgroup=""),
        ):
            with self.subTest(first=first):
                row, discovery, scan = self.inspect(first=first)
                self.assertFalse(row.verified_service_membership)
                self.assertIsNone(row.observed_processes)
                self.assertFalse(row.workload_coverage_complete)
                scan.assert_not_called()
                discovery.assert_called_once()

    def test_zero_or_unknown_cgroup_cannot_mean_work_completed(self):
        for scanned in (
            None,
            CgroupSubtreeSummary(None),
            CgroupSubtreeSummary(0, unknown=False),
            CgroupSubtreeSummary(-1, unknown=False),
            CgroupSubtreeSummary(2, unknown=True),
            CgroupSubtreeSummary(2, unknown=False, coverage_verified=True),
            CgroupSubtreeSummary(2, unknown=False, task_owner_authenticated=True),
            CgroupSubtreeSummary(4097, unknown=False),
            "invalid",
        ):
            with self.subTest(scanned=scanned):
                row, discovery, scan = self.inspect(subtree=scanned)
                self.assertFalse(row.verified_service_membership)
                self.assertIsNone(row.observed_processes)
                self.assertFalse(row.workload_coverage_complete)
                self.assertEqual(discovery.call_count, 1)
                scan.assert_called_once_with(CGROUP)

    def test_concurrent_service_restart_change_or_pid_reuse_refused(self):
        for final in (
            None,
            replace(EVIDENCE, invocation="b" * 32),
            replace(EVIDENCE, identity=replace(IDENTITY, pid=813)),
            replace(EVIDENCE, identity=replace(IDENTITY, start_ticks=12346)),
            replace(EVIDENCE, cgroup="/user.slice/other.service"),
            replace(EVIDENCE, task_owner_authenticated=True),
        ):
            with self.subTest(final=final):
                row, discovery, scan = self.inspect(last=final)
                self.assertFalse(row.verified_service_membership)
                self.assertEqual(row.reason, "service_changed_during_probe")
                self.assertIsNone(row.observed_processes)
                self.assertEqual(discovery.call_count, 2)

    def test_unknown_service_names_fail_without_scanning(self):
        for name in (
            "", "other.timer", "../example-worker.service",
            "example-worker.service;echo", "other.service\n",
            "example service.service", 123, None,
        ):
            with self.subTest(name=name):
                discover, scan = Mock(), Mock()
                row = observe_existing_user_service(
                    name, discover=discover, scan=scan,
                )
                self.assertFalse(row.verified_service_membership)
                self.assertEqual(row.reason, "invalid_or_unsupported_service")
                discover.assert_not_called()
                scan.assert_not_called()

    def test_failures_do_not_expose_private_metadata(self):
        for failing in (
            OSError("private unit name"),
            ValueError("secret process data"),
            RuntimeError("private cgroup"),
            TypeError("private data"),
        ):
            with self.subTest(failing=type(failing).__name__):
                discover = Mock(side_effect=failing)
                row = observe_existing_user_service(
                    NAME, discover=discover, scan=Mock(),
                )
                self.assertFalse(row.verified_service_membership)
                self.assertNotIn("private", repr(row))
                self.assertNotIn("secret", repr(row))

    def test_cli_reports_count_without_service_identity_or_task_safe_claim(self):
        with patch("isc.cli.observe_existing_user_service", return_value=
                   ExistingServiceObservation(
                       3, "service_process_membership_observed_task_state_unknown",
                       verified_service_membership=True,
                   )) as inspect, redirect_stdout(StringIO()) as out:
            self.assertEqual(main(["existing-service", NAME]), 0)
        text = out.getvalue()
        self.assertIn("membership: VERIFIED NOW", text)
        self.assertIn("Observed processes in service cgroup: 3", text)
        self.assertIn("Application task activity or completion: NOT VERIFIED", text)
        self.assertIn("Automatic sleep eligibility: NOT ESTABLISHED", text)
        self.assertNotIn(NAME, text)
        self.assertNotIn(CGROUP, text)
        inspect.assert_called_once_with(NAME)

    def test_cli_unverified_existing_service_does_not_claim_no_work(self):
        with patch("isc.cli.observe_existing_user_service", return_value=
                   ExistingServiceObservation(
                       None, "service_workload_unverified",
                   )), redirect_stdout(StringIO()) as out:
            self.assertEqual(main(["existing-service", NAME]), 2)
        self.assertIn("membership: NOT VERIFIED", out.getvalue())
        self.assertIn("Application task activity or completion: NOT VERIFIED", out.getvalue())
        self.assertNotIn("service finished", out.getvalue())

    def test_cli_missing_arguments_do_not_trigger_probes(self):
        with patch("isc.cli.observe_existing_user_service") as inspect:
            for args in (
                ["existing-service"],
                ["existing-service", NAME, "unrelated"],
            ):
                with redirect_stdout(StringIO()):
                    self.assertEqual(main(args), 2)
        inspect.assert_not_called()

    def test_cli_does_not_use_execution_or_power_operation(self):
        with patch("isc.cli.observe_existing_user_service", return_value=
                   ExistingServiceObservation(
                       1, "service_process_membership_observed_task_state_unknown",
                       verified_service_membership=True,
                   )), patch("isc.cli.subprocess.run",
                              side_effect=AssertionError("no subprocess in CLI")), \
             patch("isc.settings.set_mode",
                   side_effect=AssertionError("no mode change")), \
             patch("isc.cli.change_gnome_policy",
                   side_effect=AssertionError("no GNOME change")), \
             redirect_stdout(StringIO()):
            self.assertEqual(main(["existing-service", NAME]), 0)


if __name__ == "__main__":
    unittest.main()
