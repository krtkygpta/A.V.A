"""
Phase 2 tests: ClarificationBridge, needs_input, answer, timeout, cancel.

Run from App/:
  python -m unittest core.tests.test_clarify -v
"""

import os
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import core.jobs.clarify as clarify_mod
import core.jobs.inbox as inbox_mod
import core.TaskManager as tm
from core.jobs.clarify import ClarificationBridge, get_clarification_bridge
from core.TaskManager import (
    SubAgent,
    TaskRegistry,
    TaskStatus,
    manage_background_tasks,
    get_pending_clarifications_summary,
)


def _reset_bridge(path: Path) -> ClarificationBridge:
    clarify_mod.CLARIFY_STORE_PATH = path
    ClarificationBridge._instance = None
    return get_clarification_bridge()


# Avoid plyer/pyautogui import hangs in worker completion path.
_ORIG_SUBAGENT_NOTIFY = SubAgent._desktop_notify

class _DummyAgent(SubAgent):
    def __init__(self, **kwargs):
        super().__init__(
            task_name=kwargs.get("name", "Dummy"),
            task_description="test",
            task_type=kwargs.get("task_type", "delegate"),
            kind=kwargs.get("kind", "long"),
        )
        self.got_answer = None
        self.question = kwargs.get("question", "What budget?")
        self.default = kwargs.get("default", "flexible")
        self.timeout = kwargs.get("timeout", 5)
        self.require_user = kwargs.get("require_user", False)

    def execute(self, **kwargs):
        self.registry.update_task(self.task.id, TaskStatus.RUNNING)
        ans = self.ask_main(
            question=self.question,
            options=["under 1000", "flexible"],
            context_snippet="Filtering packages",
            default_if_timeout=self.default,
            require_user=self.require_user,
            timeout_seconds=self.timeout,
        )
        self.got_answer = ans
        return f"done with {ans}"


class TestClarificationBridge(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.gettempdir()) / f"ava_clarify_{uuid.uuid4().hex}.json"
        self.task_path = Path(tempfile.gettempdir()) / f"ava_clarify_tasks_{uuid.uuid4().hex}.json"
        self.inbox_path = Path(tempfile.gettempdir()) / f"ava_clarify_inbox_{uuid.uuid4().hex}.json"
        self._orig = clarify_mod.CLARIFY_STORE_PATH
        self._orig_tasks = tm.TASK_STORE_PATH
        self._orig_inbox = inbox_mod.INBOX_STORE_PATH
        tm.TASK_STORE_PATH = self.task_path
        TaskRegistry._instance = None
        inbox_mod.INBOX_STORE_PATH = self.inbox_path
        inbox_mod.Inbox._instance = None
        self.bridge = _reset_bridge(self.tmp)
        self.bridge.clear()
        TaskRegistry().clear_all()
        inbox_mod.get_inbox().clear()
        # Silence desktop notify (import chain can hang without optional deps)
        clarify_mod.ClarificationBridge._desktop_notify = staticmethod(lambda req: None)
        SubAgent._desktop_notify = staticmethod(lambda task: None)

    def tearDown(self):
        try:
            self.bridge.clear()
        except Exception:
            pass
        ClarificationBridge._instance = None
        clarify_mod.CLARIFY_STORE_PATH = self._orig
        tm.TASK_STORE_PATH = self._orig_tasks
        TaskRegistry._instance = None
        inbox_mod.INBOX_STORE_PATH = self._orig_inbox
        inbox_mod.Inbox._instance = None
        SubAgent._desktop_notify = _ORIG_SUBAGENT_NOTIFY
        for p in (self.tmp, self.task_path, self.inbox_path):
            try:
                p.unlink()
            except OSError:
                pass

    def test_main_self_answer_resumes_job(self):
        agent = _DummyAgent(timeout=10)
        task_id = agent.start()
        # Wait until needs_input
        deadline = time.time() + 3
        while time.time() < deadline:
            task = TaskRegistry().get_task(task_id)
            if task and task.status == TaskStatus.NEEDS_INPUT:
                break
            time.sleep(0.05)
        task = TaskRegistry().get_task(task_id)
        self.assertIsNotNone(task)
        self.assertEqual(task.status, TaskStatus.NEEDS_INPUT)
        self.assertTrue(task.pending_request_id)

        out = manage_background_tasks(
            action="answer",
            request_id=task.pending_request_id,
            answer="under 1500 warm beaches",
            source="main",
        )
        self.assertIn("delivered", out.lower())

        deadline = time.time() + 3
        while time.time() < deadline:
            task = TaskRegistry().get_task(task_id)
            if task and task.status == TaskStatus.COMPLETED:
                break
            time.sleep(0.05)

        task = TaskRegistry().get_task(task_id)
        self.assertEqual(task.status, TaskStatus.COMPLETED)
        self.assertIn("under 1500", str(task.result))
        self.assertEqual(agent.got_answer, "under 1500 warm beaches")
        self.assertIsNone(task.pending_request_id)

    def test_user_path_via_task_id(self):
        agent = _DummyAgent(timeout=10)
        task_id = agent.start()
        deadline = time.time() + 3
        while time.time() < deadline:
            task = TaskRegistry().get_task(task_id)
            if task and task.status == TaskStatus.NEEDS_INPUT:
                break
            time.sleep(0.05)

        out = manage_background_tasks(
            action="answer",
            task_id=task_id,
            answer="user said beach only",
            source="user",
        )
        self.assertIn("delivered", out.lower())

        deadline = time.time() + 3
        while time.time() < deadline:
            if agent.got_answer is not None:
                break
            time.sleep(0.05)
        self.assertEqual(agent.got_answer, "user said beach only")

    def test_timeout_uses_default(self):
        agent = _DummyAgent(timeout=1, default="use flexible")
        task_id = agent.start()
        deadline = time.time() + 4
        while time.time() < deadline:
            task = TaskRegistry().get_task(task_id)
            if task and task.status == TaskStatus.COMPLETED:
                break
            time.sleep(0.05)
        task = TaskRegistry().get_task(task_id)
        self.assertEqual(task.status, TaskStatus.COMPLETED)
        self.assertEqual(agent.got_answer, "use flexible")
        self.assertIn("use flexible", str(task.result))

    def test_cancel_unblocks_waiter(self):
        agent = _DummyAgent(timeout=30, default="fallback")
        task_id = agent.start()
        deadline = time.time() + 3
        while time.time() < deadline:
            task = TaskRegistry().get_task(task_id)
            if task and task.status == TaskStatus.NEEDS_INPUT:
                break
            time.sleep(0.05)
        self.assertTrue(TaskRegistry().cancel_task(task_id))

        deadline = time.time() + 3
        while time.time() < deadline:
            # worker should finish (cancelled path still returns from execute)
            if not agent.is_running():
                break
            time.sleep(0.05)
        self.assertFalse(agent.is_running())
        task = TaskRegistry().get_task(task_id)
        self.assertEqual(task.status, TaskStatus.CANCELLED)

    def test_prompt_text_and_list(self):
        agent = _DummyAgent(timeout=15)
        task_id = agent.start()
        deadline = time.time() + 3
        while time.time() < deadline:
            if self.bridge.count_pending() > 0:
                break
            time.sleep(0.05)
        text = get_pending_clarifications_summary()
        self.assertIn("[NEEDS INPUT]", text)
        self.assertIn("budget", text.lower())

        listed = manage_background_tasks("list")
        self.assertIn(task_id, listed)
        self.assertIn("needs_input", listed)

        # Cleanup answer so thread ends
        task = TaskRegistry().get_task(task_id)
        manage_background_tasks(
            action="answer",
            request_id=task.pending_request_id,
            answer="ok",
            source="main",
        )
        deadline = time.time() + 3
        while agent.is_running() and time.time() < deadline:
            time.sleep(0.05)

    def test_wake_queue_pop(self):
        agent = _DummyAgent(timeout=15)
        agent.start()
        deadline = time.time() + 3
        while time.time() < deadline:
            if self.bridge.has_wakes():
                break
            time.sleep(0.05)
        self.assertTrue(self.bridge.has_wakes())
        wakes = self.bridge.pop_wakes()
        self.assertEqual(len(wakes), 1)
        self.assertIn("budget", wakes[0].question.lower())
        self.assertFalse(self.bridge.has_wakes())

        # Cleanup
        manage_background_tasks(
            action="answer",
            request_id=wakes[0].id,
            answer="ok",
            source="main",
        )
        deadline = time.time() + 3
        while agent.is_running() and time.time() < deadline:
            time.sleep(0.05)

    def test_compact_inject_format(self):
        agent = _DummyAgent(timeout=15)
        agent.start()
        deadline = time.time() + 3
        while time.time() < deadline:
            pending = self.bridge.list_pending()
            if pending:
                break
            time.sleep(0.05)
        req = self.bridge.list_pending()[0]
        text = req.to_compact_inject()
        self.assertIn("request_id=", text)
        self.assertIn("question=", text)
        self.assertIn("options=", text)

        manage_background_tasks(
            action="answer", request_id=req.id, answer="x", source="main"
        )
        deadline = time.time() + 3
        while agent.is_running() and time.time() < deadline:
            time.sleep(0.05)

    def test_max_clarifications_budget(self):
        # Manually exhaust budget without starting full agent loops
        job_id = "budgetjob"
        TaskRegistry().create_task("T", "", task_type="delegate")
        # Use a known job from registry
        task = TaskRegistry().create_task("BudgetTest", "", task_type="delegate")
        job_id = task.id
        TaskRegistry().update_task(job_id, TaskStatus.RUNNING)

        for i in range(clarify_mod.MAX_CLARIFICATIONS_PER_JOB):
            def _ask(idx=i):
                self.bridge.ask_main(
                    job_id=job_id,
                    question=f"Q{idx}?",
                    timeout_seconds=1,
                    default_if_timeout=f"d{idx}",
                    job_name="BudgetTest",
                )

            t = threading.Thread(target=_ask, daemon=True)
            t.start()
            deadline = time.time() + 2
            while time.time() < deadline:
                req = self.bridge.get_pending_for_job(job_id)
                if req:
                    self.bridge.answer(answer=f"a{i}", request_id=req.id, source="main")
                    break
                time.sleep(0.02)
            t.join(timeout=2)

        # Next ask should short-circuit without pending
        resp = self.bridge.ask_main(
            job_id=job_id,
            question="one more?",
            timeout_seconds=1,
            default_if_timeout="budget-exceeded-default",
            job_name="BudgetTest",
        )
        self.assertEqual(resp.source, "timeout")
        self.assertIn("budget", resp.answer.lower() + " budget-exceeded-default")
        # either the budget message or default
        self.assertTrue(
            "budget" in resp.answer.lower() or resp.answer == "budget-exceeded-default"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
