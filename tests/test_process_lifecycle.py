"""Process-lifecycle probes use synthetic /proc snapshots only."""
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.process_lifecycle import (
    ProcessIdentity, ProcessLineage, compare_process_lineage,
    parse_proc_stat, read_process_lineage,
)


def proc_stat(pid, name, ppid, start, state="S"):
    # Linux fields: pid (comm) state ppid ... starttime [field 22].
    tail = [state, str(ppid)] + ["0"] * 17 + [str(start)]
    return f"{pid} ({name}) " + " ".join(tail) + "\n"


class ProcessLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def add(self, pid, name, parent=1, start=100):
        p = self.root / str(pid)
        p.mkdir()
        (p / "stat").write_text(
            proc_stat(pid, name, parent, start), encoding="utf-8",
        )

    def read(self):
        return read_process_lineage(proc_root=self.root, uid=os.getuid())

    def test_parse_linux_stat_uses_field_22_and_handles_comm_parentheses(self):
        value = parse_proc_stat(
            proc_stat(120, "weird) name", 30, 123456), 120,
        )
        self.assertEqual(
            value, ProcessIdentity(120, 123456, 30, "weird) name"),
        )
        self.assertIsNone(parse_proc_stat(proc_stat(120, "claude", 1, 40), 121))
        self.assertIsNone(parse_proc_stat("not a stat", 120))

    def test_selected_roots_and_same_user_descendants_with_arbitrary_names(self):
        self.add(300, "claude", start=200)
        self.add(301, "python3", parent=300, start=201)
        self.add(302, "sh", parent=301, start=202)
        self.add(303, "browser", parent=1)
        snap = self.read()
        self.assertIsNotNone(snap)
        self.assertEqual(tuple(x.pid for x in snap.roots), (300,))
        self.assertEqual(tuple(x.pid for x in snap.descendants), (301, 302))
        self.assertEqual(tuple(x.pid for x in snap.tracked), (300, 301, 302))
        self.assertFalse(snap.coverage_verified)

    def test_empty_candidate_inventory_is_not_complete_coverage(self):
        self.add(404, "browser", start=42)
        snap = self.read()
        self.assertEqual(snap.roots, ())
        self.assertEqual(snap.descendants, ())
        self.assertFalse(snap.coverage_verified)

    def test_reuse_of_same_pid_is_not_continuation(self):
        before = ProcessLineage(
            (ProcessIdentity(100, 20, 1, "codex"),), (), 0,
        )
        after = ProcessLineage(
            (ProcessIdentity(100, 21, 1, "codex"),), (), 0,
        )
        result = compare_process_lineage(before, after)
        self.assertEqual(result.continuing, 0)
        self.assertEqual(result.appeared, 1)
        self.assertEqual(result.disappeared, 1)
        self.assertEqual(result.reused_pids, 1)
        self.assertFalse(result.coverage_verified)

    def test_identical_pid_and_start_ticks_is_continuation_only(self):
        obj = ProcessIdentity(100, 20, 1, "claude")
        first = ProcessLineage((obj,), (), 0)
        second = ProcessLineage((obj,), (), 0)
        delta = compare_process_lineage(first, second)
        self.assertEqual(delta.continuing, 1)
        self.assertEqual(delta.appeared, 0)
        self.assertEqual(delta.disappeared, 0)
        self.assertEqual(delta.reused_pids, 0)

    def test_exited_process_is_disappeared_not_completed(self):
        first = ProcessLineage(
            (ProcessIdentity(50, 2, 1, "ollama"),), (), 0,
        )
        after = ProcessLineage((), (), 0)
        delta = compare_process_lineage(first, after)
        self.assertEqual(delta.disappeared, 1)
        self.assertFalse(hasattr(delta, "completed"))
        self.assertFalse(delta.coverage_verified)

    def test_parent_name_change_is_not_accepted_as_same_identity(self):
        a = ProcessLineage((ProcessIdentity(70, 2, 1, "claude"),), (), 0)
        b = ProcessLineage((ProcessIdentity(70, 2, 1, "codex"),), (), 0)
        delta = compare_process_lineage(a, b)
        self.assertEqual(delta.continuing, 0)
        self.assertEqual((delta.appeared, delta.disappeared), (1, 1))

    def test_unverifiable_read_is_unknown_not_zero(self):
        self.assertIsNone(read_process_lineage(
            proc_root=self.root / "missing", uid=os.getuid(),
        ))
        self.assertIsNone(compare_process_lineage(None, None))

    def test_malformed_stat_is_not_accepted(self):
        self.add(60, "make")
        (self.root / "60" / "stat").write_text("BROKEN", encoding="utf-8")
        self.assertIsNone(self.read())

    def test_suspicious_proc_entry_is_not_followed(self):
        self.add(70, "codex")
        target = self.root / "70"
        (self.root / "71").symlink_to(target, target_is_directory=True)
        snap = self.read()
        self.assertEqual(len(snap.roots), 1)
        self.assertEqual(snap.roots[0].pid, 70)

    def test_stat_is_read_without_reading_private_args_or_environment(self):
        self.add(100, "codex")
        (self.root / "100" / "cmdline").write_text("PRIVATE_ARGUMENTS")
        (self.root / "100" / "environ").write_text("SECRET_PASSWORD")
        with patch("builtins.open", wraps=open) as reading:
            snap = self.read()
        self.assertEqual(len(snap.roots), 1)
        self.assertTrue(all(
            Path(call.args[0]).name == "stat"
            for call in reading.call_args_list
        ))

    def test_partial_visibility_flags_are_conservative(self):
        before = ProcessLineage((), (), 2)
        after = ProcessLineage((), (), 1)
        delta = compare_process_lineage(before, after)
        self.assertEqual(delta.visibility_races, 3)
        self.assertFalse(delta.coverage_verified)

    def test_bad_inputs_fail_closed(self):
        self.assertIsNone(read_process_lineage(proc_root=self.root, uid=-1))
        self.assertIsNone(read_process_lineage(
            proc_root=self.root, uid=os.getuid(), names=("claude",),
        ))
        self.assertIsNone(read_process_lineage(
            proc_root=self.root, uid=os.getuid(), names=frozenset({"bad\nname"}),
        ))


if __name__ == "__main__":
    unittest.main()
