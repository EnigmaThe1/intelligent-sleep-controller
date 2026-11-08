"""Strict opt-in coding-agent adapter tests; never configure real applications."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import socket
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.cgroup_subtree import CgroupSubtreeSummary
from isc.events import EventKind
from isc.hook_payload import HookObservation, parse_hook_payload
from isc.native_agent_adapter import AgentSessionBinding, translate_agent_hook
from isc.linux_peer_process import PeerProcessEvidence
from isc.native_service_enrollment import NativeServiceProcess
from isc.policy import WorkState
from isc.process_lifecycle import ProcessIdentity, ProcessLineage
from isc.receiver import LocalMonitor
from isc.service_process_correlation import ServiceProcessEvidence

OWNER = "a" * 32
TASK = "b" * 32
ROOT = ProcessIdentity(101, 500, 1, "python3")
PIN = NativeServiceProcess(
    ROOT, "agent.service", "--user", "f" * 32, "/user.slice/agent.service",
)


def record(source="claude_code", kind="UserPromptSubmit", session="session-private"):
    return parse_hook_payload(source, json.dumps({
        "hook_event_name": kind,
        "session_id": session,
        "prompt": "private prompt not forwarded",
        "cwd": "/private/work/path",
        "tool_input": {"token": "PRIVATE_SECRET"},
    }).encode("utf-8"))


def binding(observation, **changes):
    values = dict(source=observation.source,
                  session_fingerprint=observation.session_fingerprint,
                  owner_token=OWNER, task_token=TASK)
    values.update(changes)
    return AgentSessionBinding(**values)


@contextmanager
def pair():
    left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        yield left, right
    finally:
        left.close()
        right.close()


class NativeAgentAdapterTests(unittest.TestCase):
    def test_known_codex_and_claude_events_translate_to_existing_states(self):
        for source in ("claude_code", "codex"):
            for name, expected in (
                ("UserPromptSubmit", EventKind.STARTED),
                ("PreToolUse", EventKind.ACTIVITY),
                ("PostToolUse", EventKind.ACTIVITY),
                ("SubagentStart", EventKind.CHILD_STARTED),
                ("SubagentStop", EventKind.CHILD_QUIESCED),
                ("Stop", EventKind.STOPPED),
                ("SessionEnd", EventKind.STOPPED),
            ):
                with self.subTest(source=source, name=name):
                    observed = record(source, name)
                    converted = translate_agent_hook(binding(observed), observed)
                    self.assertIsNotNone(converted)
                    self.assertIs(converted.kind, expected)
                    self.assertFalse(converted.children_quiescent)
                    self.assertEqual(converted.source, OWNER)
                    self.assertEqual(converted.task_id, TASK)

    def test_unknown_events_are_not_finished_or_human_idle(self):
        for name in ("FutureHook", "TaskFinished", "SafeToSleep"):
            observed = record("codex", name)
            self.assertIsNone(translate_agent_hook(binding(observed), observed))

    def test_claimed_session_mismatch_rejected(self):
        observed = record()
        other = record(session="different-private-session")
        self.assertIsNone(translate_agent_hook(binding(observed), other))

    def test_source_mismatch_rejected(self):
        claude = record("claude_code")
        codex = record("codex")
        self.assertIsNone(translate_agent_hook(binding(claude), codex))

    def test_invalid_binding_rejected(self):
        observed = record()
        for altered in (
            {"source": "other"},
            {"session_fingerprint": "not-a-fingerprint"},
            {"owner_token": "owner with spaces"},
            {"task_token": ""},
        ):
            with self.subTest(altered=altered):
                self.assertIsNone(translate_agent_hook(
                    binding(observed, **altered), observed,
                ))

    def test_mismatched_claimed_state_rejected(self):
        observed = record()
        forged = HookObservation(
            observed.source, observed.event_name,
            observed.session_fingerprint, WorkState.COMPLETED,
        )
        self.assertIsNone(translate_agent_hook(binding(observed), forged))

    def test_adapter_never_stores_raw_prompt_session_or_path(self):
        observed = record()
        converted = translate_agent_hook(binding(observed), observed)
        for secret in ("session-private", "private prompt",
                       "PRIVATE_SECRET", "/private/work"):
            self.assertNotIn(secret, repr(converted))

    def test_session_start_and_stop_do_not_claim_completed(self):
        for source in ("claude_code", "codex"):
            observed = record(source, "SessionStart")
            converted = translate_agent_hook(binding(observed), observed)
            self.assertIs(converted.kind, EventKind.STOPPED)
            self.assertFalse(converted.children_quiescent)

    def test_external_claim_not_accepted_without_service_pin(self):
        observed = record()
        with TemporaryDirectory() as tmp:
            with LocalMonitor(runtime_dir=tmp) as monitor:
                with pair() as sockets:
                    with patch("isc.receiver.read_linux_peer_process",
                               return_value=PeerProcessEvidence(ROOT, os.getuid())):
                        self.assertFalse(monitor.observe_bound_agent_hook(
                            binding(observed), observed, sockets[0],
                            sequence=1, now=11,
                        ))

    def test_bound_agent_lifecycle_uses_native_identity_and_ordering(self):
        start = record(kind="UserPromptSubmit")
        activity = record(kind="PreToolUse")
        stopped = record(kind="Stop")
        with TemporaryDirectory() as tmp:
            with LocalMonitor(runtime_dir=tmp) as monitor:
                supervisor = monitor._native_supervisor
                supervisor._checker = lambda *args: ServiceProcessEvidence.OBSERVED_MEMBER
                supervisor._subtree_checker = lambda _: CgroupSubtreeSummary(
                    1, unknown=False,
                )
                discovery = Mock(return_value=PIN)
                self.assertTrue(supervisor.register_native_service(
                    OWNER, TASK, service="agent.service", scope="--user",
                    discovery=discovery,
                ))
                monitor.update_native_lineage(ProcessLineage((ROOT,), (), 0), 10)
                with pair() as sockets:
                    with patch("isc.receiver.read_linux_peer_process",
                               return_value=PeerProcessEvidence(ROOT, os.getuid())):
                        self.assertTrue(monitor.observe_bound_agent_hook(
                            binding(start), start, sockets[0], sequence=1, now=11,
                        ))
                        self.assertTrue(monitor.observe_bound_agent_hook(
                            binding(start), activity, sockets[0], sequence=2, now=12,
                        ))
                        self.assertFalse(monitor.observe_bound_agent_hook(
                            binding(start), activity, sockets[0], sequence=2, now=12,
                        ))
                self.assertEqual(monitor.native_lifecycle_summary(now=12).unknown, 1)
                self.assertFalse(monitor.native_lifecycle_summary(now=12).coverage_verified)

    def test_stop_is_unknown_after_valid_sequence(self):
        started = record(kind="UserPromptSubmit")
        stopped = record(kind="Stop")
        with TemporaryDirectory() as tmp:
            with LocalMonitor(runtime_dir=tmp) as monitor:
                supervisor = monitor._native_supervisor
                supervisor._checker = lambda *args: ServiceProcessEvidence.OBSERVED_MEMBER
                supervisor._subtree_checker = lambda _: CgroupSubtreeSummary(
                    1, unknown=False,
                )
                self.assertTrue(supervisor.register_native_service(
                    OWNER, TASK, service="agent.service", scope="--user",
                    discovery=Mock(return_value=PIN),
                ))
                monitor.update_native_lineage(ProcessLineage((ROOT,), (), 0), 10)
                with pair() as sockets:
                    with patch("isc.receiver.read_linux_peer_process",
                               return_value=PeerProcessEvidence(ROOT, os.getuid())):
                        self.assertTrue(monitor.observe_bound_agent_hook(
                            binding(started), started, sockets[0],
                            sequence=1, now=11,
                        ))
                        self.assertTrue(monitor.observe_bound_agent_hook(
                            binding(started), stopped, sockets[0],
                            sequence=2, now=12,
                        ))
                self.assertEqual(monitor.native_lifecycle_summary(now=12).unknown, 1)


if __name__ == "__main__":
    unittest.main()
