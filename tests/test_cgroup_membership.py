"""Synthetic procfs tests for read-only cgroup membership evidence."""
from pathlib import Path
import os
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.cgroup_membership import (
    CgroupMembership, observe_cgroup_membership, parse_cgroup_v2, within_cgroup,
)
from isc.process_lifecycle import ProcessIdentity


def stat_line(pid: int, name: str, parent: int, ticks: int) -> str:
    return f"{pid} ({name}) S {parent} " + " ".join(
        ["0"] * 17 + [str(ticks)]
    ) + "\n"


class CgroupMembershipTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.proc = self.root / "123"
        self.proc.mkdir()
        self.identity = ProcessIdentity(123, 100, 1, "codex")
        (self.proc / "stat").write_text(stat_line(123, "codex", 1, 100))
        (self.proc / "cgroup").write_bytes(
            b"0::/user.slice/agent.service/child.scope\n"
        )

    def probe(self, scope="/user.slice/agent.service", **kw):
        return observe_cgroup_membership(
            self.identity, scope, proc_root=self.root, **kw,
        )

    def test_exact_nested_and_prefix_boundary(self):
        nested = self.probe()
        self.assertEqual(nested, CgroupMembership(True))
        self.assertFalse(nested.coverage_verified)
        self.assertEqual(self.probe("/user.slice/other.service").match, False)
        self.assertEqual(self.probe("/user.slice/agent").match, False)
        self.assertIs(within_cgroup("/user.slice/a.service2", "/user.slice/a.service"), False)
        self.assertIs(within_cgroup("/user.slice/a.service/child", "/user.slice/a.service"), True)

    def test_strict_cgroup2_format_never_infers_from_v1(self):
        self.assertEqual(parse_cgroup_v2(b"0::/user.slice/a.service\n"), "/user.slice/a.service")
        for raw in (None, b"", b"1:name=systemd:/user.slice\n",
                    b"0::/a\n0::/b\n", b"0::relative\n",
                    b"0::/foo/../bar\n", b"0::/a//b\n",
                    b"0::/a\x00b\n", b"x" * 4097):
            with self.subTest(raw=raw):
                self.assertIsNone(parse_cgroup_v2(raw))

    def test_pid_reuse_or_changed_stat_returns_unknown(self):
        wrong = ProcessIdentity(123, 101, 1, "codex")
        self.assertIsNone(observe_cgroup_membership(
            wrong, "/user.slice/agent.service", proc_root=self.root,
        ).match)
        with patch("isc.cgroup_membership._read_stat", side_effect=[
            self.identity, ProcessIdentity(123, 101, 1, "codex"),
            ProcessIdentity(123, 101, 1, "codex"),
        ]):
            self.assertIsNone(self.probe().match)

    def test_cgroup_migration_between_reads_is_unknown(self):
        with patch("isc.cgroup_membership._read_cgroup", side_effect=[
            "/user.slice/agent.service",
            "/user.slice/other.service",
        ]) as observer:
            result = self.probe()
        self.assertIsNone(result.match)
        self.assertFalse(result.coverage_verified)
        self.assertEqual(observer.call_count, 2)

    def test_cgroup_disappears_between_reads_is_unknown(self):
        with patch("isc.cgroup_membership._read_cgroup", side_effect=[
            "/user.slice/agent.service", None,
        ]):
            self.assertIsNone(self.probe().match)

    def test_stable_identity_and_membership_are_required(self):
        with patch("isc.cgroup_membership._read_stat", side_effect=[
            self.identity, self.identity, self.identity,
        ]) as identity:
            with patch("isc.cgroup_membership._read_cgroup", side_effect=[
                "/user.slice/agent.service",
                "/user.slice/agent.service",
            ]) as membership:
                self.assertTrue(self.probe().match)
        self.assertEqual(identity.call_count, 3)
        self.assertEqual(membership.call_count, 2)

    def test_unknown_for_absent_inaccessible_foreign_or_symlink(self):
        self.assertIsNone(self.probe(uid=os.getuid()+1).match)
        (self.proc / "cgroup").unlink()
        self.assertIsNone(self.probe().match)
        (self.proc / "cgroup").symlink_to(self.root / "outside")
        self.assertIsNone(self.probe().match)
        (self.proc / "cgroup").unlink()
        (self.proc / "cgroup").write_bytes(b"0::/a\n")
        (self.proc / "stat").unlink()
        self.assertIsNone(self.probe().match)

    def test_invalid_arguments_fail_closed(self):
        self.assertIsNone(self.probe("relative").match)
        self.assertIsNone(self.probe("/foo/../bar").match)
        self.assertIsNone(self.probe(uid=-1).match)
        self.assertIsNone(observe_cgroup_membership(
            self.identity, "/a", proc_root=self.root, uid=True,
        ).match)
        self.assertIsNone(observe_cgroup_membership(
            ProcessIdentity(0, 100, 1, "codex"), "/a", proc_root=self.root,
        ).match)

    def test_no_command_line_or_environment_read(self):
        (self.proc / "cmdline").write_bytes(b"PRIVATE_ARGUMENTS")
        (self.proc / "environ").write_bytes(b"PRIVATE_ENV")
        with patch("builtins.open", wraps=open) as reading:
            self.assertTrue(self.probe().match)
        self.assertTrue(all(
            Path(call.args[0]).name == "stat"
            for call in reading.call_args_list
        ))


if __name__ == "__main__":
    unittest.main()
