"""Regression: finished jobs stay readable after notify (live user bug)."""

import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import core.TaskManager as tm
from core.TaskManager import (
    CompletionQueue,
    ConversationStateManager,
    DeliveryMethod,
    NotificationDecisionEngine,
    TaskPriority,
    TaskRegistry,
    TaskStatus,
    manage_background_tasks,
    get_running_tasks_summary,
)


class TestResultAfterNotify(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.gettempdir()) / f"ava_res_{uuid.uuid4().hex}.json"
        self._orig = tm.TASK_STORE_PATH
        tm.TASK_STORE_PATH = self.path
        TaskRegistry._instance = None
        TaskRegistry().clear_all()

    def tearDown(self):
        tm.TASK_STORE_PATH = self._orig
        TaskRegistry._instance = None
        try:
            self.path.unlink()
        except OSError:
            pass

    def test_result_readable_after_mark_notified(self):
        reg = TaskRegistry()
        task = reg.create_task(
            "Delegated agent: wait 50s",
            "Respond in 50 seconds",
            task_type="delegate",
        )
        reg.update_task(
            task.id,
            TaskStatus.COMPLETED,
            result="Hi there! I’m here to help.",
        )
        reg.mark_as_notified(task.id)

        live = reg.get_task(task.id)
        self.assertEqual(live.status, TaskStatus.COMPLETED)
        self.assertIsNotNone(live.notified_at)

        out = manage_background_tasks("result", task.id)
        self.assertIn("here to help", out)
        self.assertNotIn("no final result", out.lower())

    def test_legacy_notified_status_still_returns_result(self):
        reg = TaskRegistry()
        task = reg.create_task("Old", "", task_type="delegate")
        reg.update_task(task.id, TaskStatus.COMPLETED, result="legacy body")
        # Simulate old rows that stored status=notified
        with reg._tasks_lock:
            t = reg._tasks[task.id]
            t.status = TaskStatus.NOTIFIED
            t.result = "legacy body"
            t.notified_at = t.completed_at
            reg._persist_locked()

        out = manage_background_tasks("result", task.id)
        self.assertEqual(out, "legacy body")

    def test_status_summary_includes_recent_finished(self):
        reg = TaskRegistry()
        task = reg.create_task("Done job", "", task_type="demo")
        reg.update_task(task.id, TaskStatus.COMPLETED, result="finished payload")
        summary = get_running_tasks_summary()
        self.assertIn("No background tasks are currently running", summary)
        self.assertIn("Recent finished", summary)
        self.assertIn(task.id, summary)
        self.assertIn("finished payload", summary)

    def test_orphan_demo_completion_wakes_active_chat(self):
        """Reproduce 20260801_225023: empty conversation_id + active TUI chat."""
        state = ConversationStateManager()
        state.conversation_started("20260801_225023", "Background Task Status")
        state.is_conversation_active = True
        state.current_mode = "tui"

        reg = TaskRegistry()
        task = reg.create_task(
            "Demo: Demo task will run for 50 seconds",
            "Simulated work",
            conversation_id="",
            task_type="demo",
            kind="long",
            priority=TaskPriority.NORMAL,
        )
        reg.update_task(task.id, TaskStatus.COMPLETED, result="Demo finished. ran 50s.")
        live = reg.get_task(task.id)

        strategy = NotificationDecisionEngine().decide_notification_strategy(live)
        self.assertEqual(
            strategy.delivery_method,
            DeliveryMethod.NEXT_RESPONSE,
            "Main agent must speak when user is in an active chat",
        )

    def test_bind_conversation_on_orphan(self):
        reg = TaskRegistry()
        task = reg.create_task("Orphan", "", conversation_id="", task_type="demo")
        self.assertTrue(reg.bind_conversation(task.id, "conv_x", "Name X"))
        live = reg.get_task(task.id)
        self.assertEqual(live.conversation_id, "conv_x")
        self.assertEqual(live.conversation_name, "Name X")
        # Do not steal from another conversation
        self.assertFalse(reg.bind_conversation(task.id, "conv_y", "Y"))
        self.assertEqual(reg.get_task(task.id).conversation_id, "conv_x")


if __name__ == "__main__":
    unittest.main(verbosity=2)
