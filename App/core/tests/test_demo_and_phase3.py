"""
Demo agent + Phase 3 short-job / exclusive-lane tests.

Run from App/:
  python -m unittest core.tests.test_demo_and_phase3 -v
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
from core.jobs.clarify import ClarificationBridge, get_clarification_bridge
from core.jobs.resources import ExclusiveLane, get_lane, get_resource_registry
import core.TaskManager as tm
from core.TaskManager import (
    DemoAgent,
    SmartHomeAgent,
    SubAgent,
    TaskRegistry,
    TaskStatus,
    TimerAgent,
    dispatch_background_task,
    infer_kind,
    manage_background_tasks,
)


def _isolate_task_store(path: Path):
    """Point TaskRegistry at a temp JSON file and reset the singleton."""
    tm.TASK_STORE_PATH = path
    TaskRegistry._instance = None
    return TaskRegistry()


def _reset_inbox(path: Path):
    inbox_mod.INBOX_STORE_PATH = path
    inbox_mod.Inbox._instance = None
    return inbox_mod.get_inbox()


def _reset_bridge(path: Path):
    clarify_mod.CLARIFY_STORE_PATH = path
    ClarificationBridge._instance = None
    return get_clarification_bridge()


class TestInferKindPhase3(unittest.TestCase):
    def test_demo_default_long(self):
        self.assertEqual(infer_kind("demo"), "long")

    def test_short_defaults(self):
        self.assertEqual(infer_kind("timer"), "short")
        self.assertEqual(infer_kind("scrape"), "short")
        self.assertEqual(infer_kind("smarthome"), "short")


class TestExclusiveLane(unittest.TestCase):
    def setUp(self):
        get_resource_registry().reset()

    def tearDown(self):
        get_resource_registry().reset()

    def test_acquire_release(self):
        lane = get_lane("smarthome")
        self.assertTrue(lane.acquire("job1", timeout=1))
        self.assertEqual(lane.holder(), "job1")
        self.assertTrue(lane.release("job1"))
        self.assertIsNone(lane.holder())

    def test_second_waiter_blocks_until_release(self):
        lane = ExclusiveLane("test")
        self.assertTrue(lane.acquire("a", timeout=1))
        order = []

        def waiter():
            ok = lane.acquire("b", timeout=3)
            order.append(("b", ok, time.time()))
            if ok:
                lane.release("b")

        t0 = time.time()
        th = threading.Thread(target=waiter, daemon=True)
        th.start()
        time.sleep(0.3)
        order.append(("release_a", True, time.time()))
        lane.release("a")
        th.join(timeout=3)
        self.assertTrue(any(x[0] == "b" and x[1] for x in order))
        # b should finish after a released
        release_t = next(x[2] for x in order if x[0] == "release_a")
        b_t = next(x[2] for x in order if x[0] == "b")
        self.assertGreaterEqual(b_t, release_t - 0.05)
        self.assertLess(b_t - t0, 3.0)

    def test_timeout_when_held(self):
        lane = ExclusiveLane("to")
        self.assertTrue(lane.acquire("holder", timeout=1))
        start = time.time()
        ok = lane.acquire("other", timeout=0.25)
        elapsed = time.time() - start
        self.assertFalse(ok)
        self.assertGreaterEqual(elapsed, 0.2)
        lane.release("holder")

    def test_same_job_reentrant(self):
        lane = ExclusiveLane("re")
        self.assertTrue(lane.acquire("j", timeout=1))
        self.assertTrue(lane.acquire("j", timeout=1))
        self.assertTrue(lane.release("j"))


class TestDemoAgent(unittest.TestCase):
    def setUp(self):
        self.task_path = Path(tempfile.gettempdir()) / f"ava_demo_tasks_{uuid.uuid4().hex}.json"
        self.inbox_path = Path(tempfile.gettempdir()) / f"ava_demo_in_{uuid.uuid4().hex}.json"
        self.clarify_path = Path(tempfile.gettempdir()) / f"ava_demo_cl_{uuid.uuid4().hex}.json"
        self._orig_tasks = tm.TASK_STORE_PATH
        self._orig_inbox = inbox_mod.INBOX_STORE_PATH
        self._orig_clarify = clarify_mod.CLARIFY_STORE_PATH
        _isolate_task_store(self.task_path)
        self.inbox = _reset_inbox(self.inbox_path)
        self.inbox.clear()
        self.bridge = _reset_bridge(self.clarify_path)
        self.bridge.clear()
        ClarificationBridge._desktop_notify = staticmethod(lambda r: None)
        SubAgent._desktop_notify = staticmethod(lambda t: None)

    def tearDown(self):
        try:
            self.bridge.clear()
        except Exception:
            pass
        ClarificationBridge._instance = None
        clarify_mod.CLARIFY_STORE_PATH = self._orig_clarify
        _reset_inbox(self._orig_inbox)
        tm.TASK_STORE_PATH = self._orig_tasks
        TaskRegistry._instance = None
        for p in (self.inbox_path, self.clarify_path, self.task_path):
            try:
                p.unlink()
            except OSError:
                pass

    def _wait_status(self, task_id, status, timeout=8):
        deadline = time.time() + timeout
        while time.time() < deadline:
            t = TaskRegistry().get_task(task_id)
            if t and t.status == status:
                return t
            time.sleep(0.05)
        return TaskRegistry().get_task(task_id)

    def test_demo_burns_time_and_lands_in_inbox(self):
        start = time.time()
        task_id = DemoAgent(duration=2, topic="vacation research", ask=False).start()
        task = self._wait_status(task_id, TaskStatus.COMPLETED, timeout=6)
        elapsed = time.time() - start
        self.assertIsNotNone(task)
        self.assertEqual(task.status, TaskStatus.COMPLETED)
        self.assertEqual(task.kind, "long")
        self.assertEqual(task.task_type, "demo")
        self.assertIn("vacation research", str(task.result))
        self.assertGreaterEqual(elapsed, 1.8)
        # Sticky inbox for long demo
        self.assertEqual(self.inbox.count_unread(), 1)
        self.assertEqual(self.inbox.get(task_id).title.startswith("Demo:"), True)

    def test_demo_with_ask_user_answer(self):
        task_id = DemoAgent(
            duration=3,
            topic="budget trip",
            ask=True,
            question="Budget per person?",
            require_user=True,
        ).start()
        task = self._wait_status(task_id, TaskStatus.NEEDS_INPUT, timeout=5)
        self.assertEqual(task.status, TaskStatus.NEEDS_INPUT)
        self.assertTrue(task.pending_request_id)

        out = manage_background_tasks(
            action="answer",
            request_id=task.pending_request_id,
            answer="under 1500 warm beaches",
            source="user",
        )
        self.assertIn("delivered", out.lower())

        task = self._wait_status(task_id, TaskStatus.COMPLETED, timeout=8)
        self.assertEqual(task.status, TaskStatus.COMPLETED)
        self.assertIn("under 1500", str(task.result))
        self.assertEqual(self.inbox.count_unread(), 1)

    def test_demo_short_kind_skips_inbox(self):
        task_id = DemoAgent(
            duration=2, topic="quick", ask=False, kind="short"
        ).start()
        task = self._wait_status(task_id, TaskStatus.COMPLETED, timeout=6)
        self.assertEqual(task.status, TaskStatus.COMPLETED)
        self.assertEqual(task.kind, "short")
        self.assertEqual(self.inbox.count_unread(), 0)

    def test_dispatch_demo(self):
        task_id, msg = dispatch_background_task(
            "demo",
            duration=2,
            topic="dispatch test",
            ask=False,
            conversation_id="c1",
            conversation_name="test",
        )
        self.assertTrue(task_id)
        self.assertIn(task_id, msg)
        task = self._wait_status(task_id, TaskStatus.COMPLETED, timeout=6)
        self.assertEqual(task.task_type, "demo")
        self.assertEqual(task.status, TaskStatus.COMPLETED)


class TestTimerShortPhase3(unittest.TestCase):
    def setUp(self):
        self.task_path = Path(tempfile.gettempdir()) / f"ava_tm_tasks_{uuid.uuid4().hex}.json"
        self.inbox_path = Path(tempfile.gettempdir()) / f"ava_tm_in_{uuid.uuid4().hex}.json"
        self._orig_tasks = tm.TASK_STORE_PATH
        self._orig = inbox_mod.INBOX_STORE_PATH
        _isolate_task_store(self.task_path)
        self.inbox = _reset_inbox(self.inbox_path)
        self.inbox.clear()
        SubAgent._desktop_notify = staticmethod(lambda t: None)

    def tearDown(self):
        _reset_inbox(self._orig)
        tm.TASK_STORE_PATH = self._orig_tasks
        TaskRegistry._instance = None
        for p in (self.inbox_path, self.task_path):
            try:
                p.unlink()
            except OSError:
                pass

    def test_timer_never_inbox_and_short(self):
        task_id = TimerAgent(duration_seconds=0, message="ding").start()
        deadline = time.time() + 3
        while time.time() < deadline:
            t = TaskRegistry().get_task(task_id)
            if t and t.status == TaskStatus.COMPLETED:
                break
            time.sleep(0.05)
        t = TaskRegistry().get_task(task_id)
        self.assertEqual(t.kind, "short")
        self.assertEqual(t.status, TaskStatus.COMPLETED)
        self.assertEqual(self.inbox.count_unread(), 0)


class TestSmartHomeLaneWired(unittest.TestCase):
    def setUp(self):
        self.task_path = Path(tempfile.gettempdir()) / f"ava_sh_tasks_{uuid.uuid4().hex}.json"
        self._orig_tasks = tm.TASK_STORE_PATH
        _isolate_task_store(self.task_path)
        get_resource_registry().reset()
        SubAgent._desktop_notify = staticmethod(lambda t: None)

    def tearDown(self):
        get_resource_registry().reset()
        tm.TASK_STORE_PATH = self._orig_tasks
        TaskRegistry._instance = None
        try:
            self.task_path.unlink()
        except OSError:
            pass

    def test_smarthome_is_short_kind(self):
        agent = SmartHomeAgent("turn lights blue")
        self.assertEqual(agent.task.kind, "short")
        self.assertEqual(agent.task.task_type, "smarthome")

    def test_smarthome_execute_lane_pattern(self):
        """Mirror SmartHomeAgent's acquire/run/release pattern without importing device stack."""
        lane = get_lane("smarthome")
        job_id = "sh_job_1"
        held_during = []

        def run_like_smarthome(command: str) -> str:
            if not lane.acquire(job_id, timeout=2):
                return "lane busy"
            try:
                held_during.append(lane.holder())
                if command == "boom":
                    raise RuntimeError("device offline")
                return f"ok:{command}"
            finally:
                lane.release(job_id)

        self.assertEqual(run_like_smarthome("blue"), "ok:blue")
        self.assertEqual(held_during[-1], job_id)
        self.assertIsNone(lane.holder())

        with self.assertRaises(RuntimeError):
            run_like_smarthome("boom")
        self.assertIsNone(lane.holder())


if __name__ == "__main__":
    unittest.main(verbosity=2)
