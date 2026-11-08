"""Opt-in native supervisor integrated into MONITOR without socket enrollment."""
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.background_observer import BackgroundProcessObserver
from isc.policy import WorkState
from isc.process_lifecycle import ProcessIdentity, ProcessLineage
from isc.receiver import LocalMonitor
from isc.service_process_correlation import ServiceProcessEvidence

OWNER = "a" * 32
TASK = "b" * 32
ROOT = ProcessIdentity(123, 20, 1, "codex")
CHILD = ProcessIdentity(124, 21, 123, "python3")


def lineage(*children):
    return ProcessLineage((ROOT,), children, 0)


class MonitorNativeSupervisionTests(unittest.TestCase):
    def test_no_enrollment_has_no_native_queries(self):
        with TemporaryDirectory() as temp:
            with LocalMonitor(runtime_dir=temp) as monitor:
                initial = monitor.native_supervision_summary()
                self.assertEqual(initial.registered, 0)
                self.assertEqual(initial.observed_members, 0)
                with patch.object(
                    monitor._native_supervisor, "observe_snapshot",
                ) as scan:
                    monitor.update_native_lineage(lineage(CHILD), 1.0)
                    scan.assert_not_called()
                self.assertEqual(monitor.native_supervision_summary().registered, 0)

    def test_explicit_enrollment_reuses_shared_process_census(self):
        with TemporaryDirectory() as temp:
            with LocalMonitor(runtime_dir=temp) as monitor:
                checker = Mock(return_value=ServiceProcessEvidence.OBSERVED_MEMBER)
                monitor._native_supervisor._checker = checker
                monitor.enroll_native_work(
                    OWNER, TASK, ROOT, service="agent.service", scope="--user",
                )
                result = monitor.update_native_lineage(lineage(CHILD), 1.0)
                self.assertEqual(result.observed_members, 1)
                self.assertEqual(result.unknown, 0)
                self.assertEqual(checker.call_count, 2)
                self.assertFalse(result.coverage_verified)
                self.assertFalse(result.task_owner_authenticated)
                self.assertEqual(
                    monitor._native_supervisor.states(), (WorkState.ACTIVE,),
                )
                # No task metadata enters the hook transport/receiver counts.
                self.assertEqual(monitor.accepted, 0)
                self.assertEqual(monitor.rejected, 0)

    def test_child_disappearance_sticky_even_after_return(self):
        with TemporaryDirectory() as temp:
            with LocalMonitor(runtime_dir=temp) as monitor:
                monitor._native_supervisor._checker = (
                    lambda *a: ServiceProcessEvidence.OBSERVED_MEMBER
                )
                monitor.enroll_native_work(
                    OWNER, TASK, ROOT, service="agent.service", scope="--user",
                )
                monitor.update_native_lineage(lineage(CHILD), 1.0)
                after = monitor.update_native_lineage(lineage(), 2.0)
                self.assertEqual(after.unknown, 1)
                self.assertEqual(after.observed_members, 0)
                restored = monitor.update_native_lineage(lineage(CHILD), 3.0)
                self.assertEqual(restored.unknown, 1)
                self.assertFalse(restored.coverage_verified)

    def test_existing_background_observer_callback_is_optional(self):
        from isc.background_observer import ProcessPulse
        pulses = []
        observations = []
        observer = None
        def publish(pulse):
            pulses.append(pulse)
            observer._stop.set()
        observer = BackgroundProcessObserver(
            publish, interval_seconds=1, scanner=lambda: lineage(CHILD),
            on_snapshot=lambda snapshot, now: observations.append(snapshot),
        )
        observer._run()
        self.assertEqual(len(pulses), 1)
        self.assertEqual(observations, [lineage(CHILD)])
        self.assertFalse(pulses[0].coverage_verified)

    def test_native_callback_failure_cannot_stop_background_census(self):
        pulses = []
        observer = None
        def publish(pulse):
            pulses.append(pulse)
            observer._stop.set()
        def broken(_snapshot, _now):
            raise RuntimeError("synthetic callback error")
        observer = BackgroundProcessObserver(
            publish, interval_seconds=1, scanner=lambda: lineage(CHILD),
            on_snapshot=broken,
        )
        observer._run()
        self.assertEqual(pulses[0].roots, 1)
        self.assertEqual(pulses[0].descendants, 1)
        self.assertFalse(pulses[0].coverage_verified)

    def test_no_socket_enrollment_protocol_is_accepted(self):
        from isc.receiver import decode_observation
        import json
        claim = json.dumps({
            "source": "codex", "event_name": "RegisterNativeWork",
            "session_fingerprint": "c" * 32,
            "pid": 123, "service": "agent.service",
        }).encode()
        self.assertIsNone(decode_observation(claim))

    def test_refuses_invalid_enrollment_without_mutating_existing(self):
        with TemporaryDirectory() as temp:
            with LocalMonitor(runtime_dir=temp) as monitor:
                with self.assertRaises(ValueError):
                    monitor.enroll_native_work(
                        OWNER, TASK, ROOT, service="bad.timer", scope="--user",
                    )
                self.assertEqual(monitor.native_supervision_summary().registered, 0)

    def test_systemd_enrollment_uses_internal_native_verification(self):
        with TemporaryDirectory() as temp:
            with LocalMonitor(runtime_dir=temp) as monitor:
                with patch.object(
                    monitor._native_supervisor, "register_native_service",
                    return_value=True,
                ) as enrollment:
                    self.assertTrue(monitor.enroll_systemd_service(
                        OWNER, TASK, service="agent.service", scope="--user",
                    ))
                    enrollment.assert_called_once_with(
                        OWNER, TASK, service="agent.service", scope="--user",
                    )
                # No socket events or unverified process data are accepted.
                self.assertEqual((monitor.accepted, monitor.rejected), (0, 0))
                self.assertFalse(monitor.native_supervision_summary().coverage_verified)

    def test_failed_systemd_enrollment_does_not_create_binding(self):
        with TemporaryDirectory() as temp:
            with LocalMonitor(runtime_dir=temp) as monitor:
                with patch(
                    "isc.native_work_supervisor.discover_native_service_process",
                    return_value=None,
                ):
                    # Discovery is the method's captured default, so patch
                    # the supervisor's native registration at the boundary.
                    with patch.object(
                        monitor._native_supervisor, "register_native_service",
                        return_value=False,
                    ):
                        self.assertFalse(monitor.enroll_systemd_service(
                            OWNER, TASK, service="agent.service", scope="--user",
                        ))
                self.assertEqual(monitor.native_supervision_summary().registered, 0)

    def test_callback_never_confers_sleep_authority(self):
        with TemporaryDirectory() as temp:
            with LocalMonitor(runtime_dir=temp) as monitor:
                monitor._native_supervisor._checker = (
                    lambda *a: ServiceProcessEvidence.OBSERVED_MEMBER
                )
                monitor.enroll_native_work(
                    OWNER, TASK, ROOT, service="agent.service", scope="--user",
                )
                output = monitor.update_native_lineage(lineage(CHILD), 1.0)
                self.assertFalse(output.coverage_verified)
                self.assertFalse(output.task_owner_authenticated)


if __name__ == "__main__":
    unittest.main()
