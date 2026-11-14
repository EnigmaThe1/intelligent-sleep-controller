"""Synthetic CPU progress evidence: no real workloads or power actions."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import os
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.process_lifecycle import ProcessIdentity, ProcessLineage
from isc.process_cpu_evidence import (
    CpuProgress, CpuSample, compare_cpu_samples,
    parse_cpu_stat, read_cpu_samples,
)
from isc.background_observer import BackgroundProcessObserver, ProcessPulse
from isc.receiver import LocalMonitor

A = ProcessIdentity(101, 200, 1, "worker")
B = ProcessIdentity(102, 300, 101, "child")


def stat_line(identity, utime=1, stime=2):
    # field 3 state, field 4 parent, fields 14/15 CPU, field 22 starttime.
    fields = ["S", str(identity.parent_pid)] + ["0"] * 9
    fields += [str(utime), str(stime)]
    fields += ["0"] * 6
    fields += [str(identity.start_ticks)]
    return f"{identity.pid} ({identity.name}) " + " ".join(fields) + "\n"


class CpuEvidenceTests(unittest.TestCase):
    def test_stat_parser_extracts_cpu_only_after_identity_match(self):
        self.assertEqual(parse_cpu_stat(stat_line(A, 10, 20), A),
                         CpuSample(A, 30))
        self.assertIsNone(parse_cpu_stat(stat_line(A, 10, 20),
                                         ProcessIdentity(101, 201, 1, "worker")))
        self.assertIsNone(parse_cpu_stat(stat_line(A, "no", 2), A))
        self.assertIsNone(parse_cpu_stat(stat_line(A, 2**65, 2), A))

    def test_double_read_of_same_user_proc_stat(self):
        with TemporaryDirectory() as temp:
            directory = Path(temp) / "101"
            directory.mkdir()
            (directory / "stat").write_text(stat_line(A, 10, 20))
            result = read_cpu_samples(
                ProcessLineage((A,), (), 0),
                proc_root=temp, uid=os.getuid(),
            )
        self.assertEqual(result, {101: CpuSample(A, 30)})

    def test_missing_proc_or_wrong_uid_is_unknown(self):
        with TemporaryDirectory() as temp:
            self.assertIsNone(read_cpu_samples(
                ProcessLineage((A,), (), 0), proc_root=temp, uid=os.getuid(),
            ))
            directory = Path(temp) / "101"
            directory.mkdir()
            (directory / "stat").write_text(stat_line(A))
            self.assertIsNone(read_cpu_samples(
                ProcessLineage((A,), (), 0),
                proc_root=temp, uid=os.getuid() + 1,
            ))

    def test_progress_only_when_same_process_gained_ticks(self):
        previous = {101: CpuSample(A, 10), 102: CpuSample(B, 20)}
        current = {101: CpuSample(A, 15), 102: CpuSample(B, 20)}
        self.assertEqual(
            compare_cpu_samples(previous, current),
            CpuProgress(1, 1, 0),
        )
        self.assertFalse(compare_cpu_samples(previous, current).coverage_verified)

    def test_pid_reuse_never_counts_as_cpu_progress(self):
        changed = ProcessIdentity(A.pid, A.start_ticks + 10, 1, A.name)
        old = {101: CpuSample(A, 5)}
        new = {101: CpuSample(changed, 100)}
        self.assertEqual(compare_cpu_samples(old, new),
                         CpuProgress(0, 0, 1))

    def test_disappearance_and_new_process_never_mean_completion(self):
        old = {101: CpuSample(A, 5)}
        new = {102: CpuSample(B, 3)}
        result = compare_cpu_samples(old, new)
        self.assertEqual(result.changed_or_missing, 2)
        self.assertEqual(result.progressing, 0)
        self.assertFalse(result.task_owner_authenticated)

    def test_counter_regression_and_missing_samples_unknown(self):
        old = {101: CpuSample(A, 10)}
        self.assertIsNone(compare_cpu_samples(old, None).progressing)
        self.assertIsNone(compare_cpu_samples(old, {101: CpuSample(A, 9)}).progressing)

    def test_observer_correlates_cpu_and_lineage_without_completion(self):
        scans = iter([ProcessLineage((A,), (), 0)] * 2)
        cpu = iter([{101: CpuSample(A, 10)}, {101: CpuSample(A, 12)}])
        pulses = []
        worker = None
        def publish(value):
            pulses.append(value)
            if len(pulses) == 2:
                worker._stop.set()
        worker = BackgroundProcessObserver(
            publish, scanner=lambda: next(scans),
            cpu_scanner=lambda snapshot: next(cpu),
            interval_seconds=1,
        )
        worker._run()
        self.assertIsNone(pulses[0].cpu_progressing)
        self.assertEqual(pulses[1].cpu_progressing, 1)
        self.assertEqual(pulses[1].cpu_unchanged, 0)
        self.assertFalse(pulses[1].coverage_verified)

    def test_cpu_scan_failure_stays_unknown_even_with_processes(self):
        samples = iter([ProcessLineage((A,), (), 0)] * 2)
        calls = iter([{101: CpuSample(A, 5)}, None])
        pulses = []
        worker = None
        def publish(value):
            pulses.append(value)
            if len(pulses) == 2:
                worker._stop.set()
        worker = BackgroundProcessObserver(
            publish, scanner=lambda: next(samples),
            cpu_scanner=lambda snapshot: next(calls),
            interval_seconds=1,
        )
        worker._run()
        self.assertIsNone(pulses[1].cpu_progressing)
        self.assertEqual(pulses[1].roots, 1)

    def test_health_cpu_fields_stale_or_missing_are_unknown(self):
        with TemporaryDirectory() as temp:
            with LocalMonitor(runtime_dir=temp) as monitor:
                now = __import__("time").monotonic()
                monitor.update_process_pulse(ProcessPulse(
                    1, 0, 0, 0, now, cpu_progressing=1,
                    cpu_unchanged=0, cpu_changed_or_missing=0,
                ))
                peer = Mock()
                monitor._reply_health(peer)
                obj = json.loads(peer.sendall.call_args.args[0])
                self.assertEqual(obj["process_cpu_progressing"], 1)
                self.assertFalse(obj["coverage_verified"])
                peer.reset_mock()
                with patch("isc.receiver.time.monotonic", return_value=now+121):
                    monitor._reply_health(peer)
                obj = json.loads(peer.sendall.call_args.args[0])
                self.assertIsNone(obj["process_cpu_progressing"])
                self.assertIsNone(obj["process_cpu_unchanged"])

    def test_no_cpu_data_does_not_convert_to_zero(self):
        with TemporaryDirectory() as temp:
            with LocalMonitor(runtime_dir=temp) as monitor:
                now = __import__("time").monotonic()
                monitor.update_process_pulse(ProcessPulse(1, 0, 0, 0, now))
                peer = Mock()
                monitor._reply_health(peer)
                obj = json.loads(peer.sendall.call_args.args[0])
                self.assertIsNone(obj["process_cpu_progressing"])
                self.assertIsNone(obj["process_cpu_unchanged"])


if __name__ == "__main__":
    unittest.main()
