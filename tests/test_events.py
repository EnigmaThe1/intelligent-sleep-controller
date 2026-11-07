import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.events import (
    EventKind, LifecycleEvent, hook_observation, interpret, register_event,
)
from isc.policy import WorkState
from isc.registry import WorkRegistry


def event(kind, *, quiescent=False):
    return LifecycleEvent(
        source="example", task_id="task1", kind=kind,
        children_quiescent=quiescent,
    )


class LifecycleTests(unittest.TestCase):
    def test_started_and_tool_activity_active(self):
        for kind in (EventKind.STARTED, EventKind.ACTIVITY):
            with self.subTest(kind=kind):
                self.assertEqual(interpret(event(kind)).state, WorkState.ACTIVE)

    def test_external_wait_not_safe(self):
        self.assertEqual(
            interpret(event(EventKind.WAITING_EXTERNAL)).state,
            WorkState.WAITING_EXTERNAL,
        )

    def test_permission_prompt_may_be_auto_resolved(self):
        for quiescent in (False, True):
            self.assertEqual(
                interpret(event(EventKind.PERMISSION_REQUESTED,
                                quiescent=quiescent)).state,
                WorkState.UNKNOWN,
            )

    def test_stop_hook_never_proves_completed(self):
        for quiescent in (False, True):
            self.assertEqual(
                interpret(event(EventKind.STOPPED,
                                quiescent=quiescent)).state,
                WorkState.UNKNOWN,
            )

    def test_human_blocker_requires_confirmed_child_quiescence(self):
        unknown = interpret(event(EventKind.HUMAN_INPUT_CONFIRMED))
        self.assertEqual(unknown.state, WorkState.UNKNOWN)
        self.assertTrue(unknown.active_children)
        safe = interpret(event(EventKind.HUMAN_INPUT_CONFIRMED, quiescent=True))
        self.assertEqual(safe.state, WorkState.WAITING_HUMAN)
        self.assertFalse(safe.active_children)

    def test_completion_requires_confirmed_child_quiescence(self):
        self.assertEqual(
            interpret(event(EventKind.FINISHED)).state, WorkState.UNKNOWN,
        )
        self.assertEqual(
            interpret(event(EventKind.FINISHED, quiescent=True)).state,
            WorkState.COMPLETED,
        )

    def test_child_start_remains_active(self):
        interpretation = interpret(event(EventKind.CHILD_STARTED))
        self.assertEqual(interpretation.state, WorkState.ACTIVE)
        self.assertTrue(interpretation.active_children)

    def test_child_stopping_does_not_finish_parent(self):
        self.assertEqual(
            interpret(event(EventKind.CHILD_QUIESCED, quiescent=True)).state,
            WorkState.UNKNOWN,
        )

    def test_strict_invalid_kind_and_flag(self):
        with self.assertRaises(ValueError):
            interpret(event("completed"))
        with self.assertRaises(ValueError):
            interpret(LifecycleEvent("example", "task1",
                                     EventKind.FINISHED, "true"))

    def test_authenticated_adapter_contract_updates_registry(self):
        registry = WorkRegistry()
        register_event(registry, event(EventKind.STARTED),
                       now=100, ttl_seconds=10)
        self.assertEqual(
            registry.observed_work(now=101)[0].state, WorkState.ACTIVE,
        )
        register_event(registry, event(EventKind.HUMAN_INPUT_CONFIRMED,
                                       quiescent=True),
                       now=102, ttl_seconds=10)
        self.assertEqual(
            registry.observed_work(now=103)[0].state, WorkState.WAITING_HUMAN,
        )
        self.assertEqual(
            registry.observed_work(now=113)[0].state, WorkState.UNKNOWN,
        )

    def test_out_of_order_completion_cannot_overwrite_live_task(self):
        registry = WorkRegistry()
        register_event(registry, event(EventKind.STARTED),
                       now=300, ttl_seconds=10)
        with self.assertRaises(ValueError):
            register_event(registry, event(EventKind.FINISHED, quiescent=True),
                           now=299, ttl_seconds=10)
        self.assertEqual(
            registry.observed_work(now=302)[0].state, WorkState.ACTIVE,
        )

    def test_no_credentials_or_prompts_in_event_contract(self):
        event_fields = set(LifecycleEvent.__dataclass_fields__)
        self.assertEqual(
            event_fields,
            {"source", "task_id", "kind", "children_quiescent"},
        )

    def test_codex_hooks_are_conservative(self):
        self.assertEqual(
            interpret(hook_observation(
                "codex", "UserPromptSubmit", "session1"
            )).state, WorkState.ACTIVE
        )
        for event_name in ("PermissionRequest", "Stop", "SessionEnd",
                           "UnexpectedFutureHook"):
            with self.subTest(event_name=event_name):
                self.assertEqual(
                    interpret(hook_observation(
                        "codex", event_name, "session1"
                    )).state,
                    WorkState.UNKNOWN,
                )

    def test_claude_hooks_are_conservative(self):
        self.assertEqual(
            interpret(hook_observation(
                "claude_code", "PreToolUse", "session1"
            )).state, WorkState.ACTIVE
        )
        self.assertEqual(
            interpret(hook_observation(
                "claude_code", "PermissionRequest", "session1"
            )).state, WorkState.UNKNOWN
        )
        self.assertEqual(
            interpret(hook_observation(
                "claude_code", "Stop", "session1"
            )).state, WorkState.UNKNOWN
        )

    def test_codex_app_server_turn_completed_not_task_complete(self):
        self.assertEqual(
            interpret(hook_observation(
                "codex_app_server", "turn/completed", "session1"
            )).state, WorkState.UNKNOWN
        )

    def test_unrecognised_hook_never_safely_classified(self):
        e = hook_observation("other_tool", "unknown", "session1")
        self.assertEqual(interpret(e).state, WorkState.UNKNOWN)


if __name__ == "__main__":
    unittest.main()
