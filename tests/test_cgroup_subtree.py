"""Synthetic cgroup-v2 subtree tests; no real processes or system changes."""
from pathlib import Path
import os
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.cgroup_membership import CgroupMembership
from isc.cgroup_subtree import CgroupSubtreeSummary, observe_cgroup_subtree
from isc.process_lifecycle import ProcessIdentity

CGROUP = "/user.slice/agent.service"


class CgroupSubtreeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.service = self.root / "user.slice" / "agent.service"
        self.service.mkdir(parents=True)
        (self.service / "cgroup.procs").write_text("101\n")
        self.child = self.service / "child.scope"
        self.child.mkdir()
        (self.child / "cgroup.procs").write_text("102\n")
        self.lookup = Mock(side_effect=lambda pid, **kw: ProcessIdentity(
            pid, pid + 500, 1, "worker",
        ))
        self.probe = Mock(return_value=CgroupMembership(True))

    def observe(self, **kwargs):
        return observe_cgroup_subtree(
            CGROUP, cgroup_root=self.root, uid=os.getuid(),
            identity_lookup=self.lookup, membership_probe=self.probe,
            **kwargs,
        )

    def test_nested_reparented_process_counted_without_ppid_tracking(self):
        result = self.observe()
        self.assertEqual(result, CgroupSubtreeSummary(2, unknown=False))
        self.assertEqual(self.lookup.call_count, 4)
        self.assertEqual(self.probe.call_count, 4)
        self.assertFalse(result.coverage_verified)
        self.assertFalse(result.task_owner_authenticated)
        self.assertNotIn("agent.service", repr(result))
        self.assertNotIn("101", repr(result))

    def test_empty_inventory_is_never_complete_coverage(self):
        (self.service / "cgroup.procs").write_text("")
        (self.child / "cgroup.procs").write_text("")
        result = self.observe()
        self.assertEqual(result.observed_processes, 0)
        self.assertFalse(result.unknown)
        self.assertFalse(result.coverage_verified)
        self.assertFalse(result.task_owner_authenticated)

    def test_foreign_or_missing_process_is_unknown_not_zero(self):
        self.lookup.return_value = None
        self.lookup.side_effect = None
        result = self.observe()
        self.assertTrue(result.unknown)
        self.assertIsNone(result.observed_processes)

    def test_process_outside_cgroup_unknown(self):
        self.probe.return_value = CgroupMembership(False)
        self.assertTrue(self.observe().unknown)
        self.probe.return_value = CgroupMembership(None)
        self.assertTrue(self.observe().unknown)

    def test_migration_or_disappearance_between_scans_is_unknown(self):
        from isc import cgroup_subtree
        original = cgroup_subtree._collect_pids
        calls = 0
        def drift(root, path):
            nonlocal calls
            calls += 1
            if calls == 2:
                (self.child / "cgroup.procs").write_text("103\n")
            return original(root, path)
        with patch("isc.cgroup_subtree._collect_pids", side_effect=drift):
            result = self.observe()
        self.assertTrue(result.unknown)
        self.assertIsNone(result.observed_processes)

    def test_invalid_paths_refused_without_reading(self):
        for path in ("/", "", "/foo/../bar", "/foo//bar", "relative",
                     "/foo/./bar", "/foo/"):
            with self.subTest(path=path):
                with patch("isc.cgroup_subtree._collect_pids") as walk:
                    result = observe_cgroup_subtree(
                        path, cgroup_root=self.root, uid=os.getuid(),
                    )
                self.assertTrue(result.unknown)
                walk.assert_not_called()

    def test_malformed_or_duplicate_pid_and_missing_file_are_unknown(self):
        for value in ("101\n101\n", "hello\n", "0\n", "-1\n",
                      "99999999\n", "101\n\n"):
            with self.subTest(value=value):
                (self.service / "cgroup.procs").write_text(value)
                self.assertTrue(self.observe().unknown)
        (self.service / "cgroup.procs").unlink()
        self.assertTrue(self.observe().unknown)

    def test_symlinks_and_excessive_size_fail_closed(self):
        link = self.service / "elsewhere.scope"
        link.symlink_to(self.child, target_is_directory=True)
        self.assertTrue(self.observe().unknown)
        link.unlink()
        (self.service / "cgroup.procs").write_bytes(b"1\n" * 20000)
        self.assertTrue(self.observe().unknown)

    def test_permission_or_lookup_exception_fails_closed(self):
        self.lookup.side_effect = OSError("denied")
        self.assertTrue(self.observe().unknown)

    def test_duplicate_pid_across_child_cgroups_is_unknown(self):
        (self.child / "cgroup.procs").write_text("101\n")
        self.assertTrue(self.observe().unknown)

    def test_pid_reuse_with_unchanged_pid_list_is_unknown(self):
        """A detached worker can be replaced while its PID stays listed."""
        calls = {101: 0, 102: 0}
        def lookup(pid, **_):
            calls[pid] += 1
            start = pid + 500 + (1 if pid == 102 and calls[pid] > 1 else 0)
            return ProcessIdentity(pid, start, 1, "worker")
        self.lookup.side_effect = lookup
        result = self.observe()
        self.assertTrue(result.unknown)
        self.assertIsNone(result.observed_processes)

    def test_cgroup_membership_change_after_second_inventory_is_unknown(self):
        counts = {101: 0, 102: 0}
        def membership(identity, *_, **__):
            counts[identity.pid] += 1
            return CgroupMembership(
                False if identity.pid == 102 and counts[102] > 1 else True
            )
        self.probe.side_effect = membership
        result = self.observe()
        self.assertTrue(result.unknown)
        self.assertIsNone(result.observed_processes)

    def test_second_pass_lookup_exception_is_unknown(self):
        counts = {101: 0, 102: 0}
        def lookup(pid, **_):
            counts[pid] += 1
            if pid == 101 and counts[pid] > 1:
                raise OSError("worker exited")
            return ProcessIdentity(pid, pid + 500, 1, "worker")
        self.lookup.side_effect = lookup
        self.assertTrue(self.observe().unknown)

    def test_stable_second_identity_pass_returns_count_only(self):
        result = self.observe()
        self.assertEqual(result, CgroupSubtreeSummary(2, unknown=False))
        self.assertEqual(self.lookup.call_count, 4)
        self.assertEqual(self.probe.call_count, 4)
        self.assertNotIn("worker", repr(result))
        self.assertNotIn("101", repr(result))

    def test_no_process_args_or_environment_access(self):
        with patch("builtins.open", side_effect=AssertionError("unexpected read")):
            result = self.observe()
        self.assertFalse(result.unknown)


if __name__ == "__main__":
    unittest.main()
