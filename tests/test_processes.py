import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.processes import (
    DEFAULT_CANDIDATE_NAMES, ProcessCandidate, ProcessScan,
    read_process_candidates,
)


class ProcessTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def add(self, pid, comm):
        item = self.root / str(pid)
        item.mkdir()
        (item / "comm").write_text(comm, encoding="utf-8")

    def scan(self, **kwargs):
        return read_process_candidates(proc_root=self.root, uid=os.getuid(), **kwargs)

    def test_no_processes_not_proof_of_workload_coverage(self):
        self.assertEqual(self.scan(), ProcessScan(()))

    def test_only_matching_names_in_current_user_dirs(self):
        self.add(203, "codex\n")
        self.add(100, "claude\n")
        self.add(501, "browser\n")
        self.add(22, "ollama\n")
        (self.root / "not-a-pid").mkdir()
        result = self.scan()
        self.assertEqual(result.count, 3)
        self.assertEqual(
            result.candidates,
            (
                ProcessCandidate(22, "ollama"),
                ProcessCandidate(100, "claude"),
                ProcessCandidate(203, "codex"),
            ),
        )

    def test_codex_child_process_name(self):
        self.add(30, "codex-code-mode\n")
        self.assertEqual(self.scan().count, 1)

    def test_never_reads_arguments_environment_or_cwd(self):
        self.add(500, "codex\n")
        (self.root / "500" / "cmdline").write_text("SECRET_ARGS", encoding="utf-8")
        (self.root / "500" / "environ").write_text("SECRET_PASSWORD", encoding="utf-8")
        with patch("builtins.open", wraps=open) as reading:
            result = self.scan()
        self.assertEqual(result.count, 1)
        for args, _ in (call for call in reading.call_args_list):
            self.assertEqual(Path(args[0]).name, "comm")

    def test_custom_names_are_exact_not_substring(self):
        self.add(30, "claude-helper\n")
        self.add(31, "claude\n")
        self.assertEqual(self.scan(names=frozenset({"claude"})).count, 1)

    def test_missing_proc_is_unknown_not_empty(self):
        result = read_process_candidates(proc_root=self.root / "missing", uid=os.getuid())
        self.assertIsNone(result)

    def test_unreadable_candidate_is_unknown(self):
        self.add(2, "codex\n")
        with patch("builtins.open", side_effect=PermissionError("no access")):
            self.assertIsNone(self.scan())

    def test_process_exiting_during_scan_can_be_skipped(self):
        self.add(2, "codex\n")
        with patch("builtins.open", side_effect=FileNotFoundError("process gone")):
            self.assertEqual(self.scan(), ProcessScan(()))

    def test_invalid_comm_fails_closed(self):
        self.add(101, "bad\x00name\n")
        self.assertIsNone(self.scan())

    def test_invalid_utf8_comm_is_reported_as_partial_visibility(self):
        self.add(100, "claude\n")
        bad = self.root / "101"
        bad.mkdir()
        (bad / "comm").write_bytes(bytes([255, 254, 10]))
        result = self.scan()
        self.assertIsNotNone(result)
        self.assertEqual(result.count, 1)
        self.assertEqual(result.candidates[0].name, "claude")
        self.assertEqual(result.undecodable_names, 1)

    def test_invalid_names_and_uid_fail_closed(self):
        self.assertIsNone(read_process_candidates(proc_root=self.root, uid=-1))
        self.assertIsNone(self.scan(names=frozenset({"bad\nname"})))
        self.assertIsNone(self.scan(names=("codex",)))

    def test_names_are_not_classified_as_busy_or_idle(self):
        self.add(100, "codex\n")
        candidate = self.scan().candidates[0]
        self.assertFalse(hasattr(candidate, "state"))
        self.assertEqual(candidate.name, "codex")


if __name__ == "__main__":
    unittest.main()
