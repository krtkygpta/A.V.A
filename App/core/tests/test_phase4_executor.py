"""Phase 4: executor concurrency, cancel events, orphan reclaim, model router."""

import os
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import core.TaskManager as tm
from core.TaskManager import (
    DemoAgent,
    SubAgent,
    TaskRegistry,
    TaskStatus,
    dispatch_background_task,
)
from core.jobs.executor import JobExecutor, get_executor
from core.jobs.model_router import infer_model_tier, resolve_model_name
from core.jobs.context import clip_text, write_artifact, compact_worker_user_pack


class TestModelRouter(unittest.TestCase):
    def test_infer_defaults(self):
        self.assertEqual(infer_model_tier("timer"), "none")
        self.assertEqual(infer_model_tier("demo"), "none")
        self.assertEqual(infer_model_tier("delegate"), "small")
        self.assertEqual(infer_model_tier("research"), "heavy")
        self.assertEqual(infer_model_tier("mission"), "main")

    def test_difficulty_bump(self):
        self.assertEqual(infer_model_tier("delegate", "hard"), "main")
        self.assertEqual(infer_model_tier("delegate", "easy"), "small")

    def test_explicit_override(self):
        self.assertEqual(infer_model_tier("timer", explicit="small"), "small")

    def test_resolve_none(self):
        self.assertIsNone(resolve_model_name("none"))


class TestContextPacks(unittest.TestCase):
    def test_clip(self):
        self.assertEqual(clip_text("hi", 10), "hi")
        self.assertTrue(clip_text("x" * 100, 20).endswith("..."))

    def test_worker_pack(self):
        text = compact_worker_user_pack("Do X", "ctx", "out")
        self.assertIn("Objective", text)
        self.assertIn("Do X", text)

    def test_artifact_write(self):
        path = write_artifact(
            "t_art_" + uuid.uuid4().hex[:6],
            "A" * 80,
            title="Test",
        )
        self.assertIsNotNone(path)
        self.assertTrue(Path(path).exists())
        Path(path).unlink(missing_ok=True)


class TestJobExecutor(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.gettempdir()) / f"ava_ex_{uuid.uuid4().hex}.json"
        self._orig = tm.TASK_STORE_PATH
        tm.TASK_STORE_PATH = self.path
        TaskRegistry._instance = None
        get_executor().reset_for_tests(max_jobs=2)
        TaskRegistry().clear_all()

    def tearDown(self):
        get_executor().reset_for_tests(max_jobs=4)
        tm.TASK_STORE_PATH = self._orig
        TaskRegistry._instance = None
        try:
            self.path.unlink()
        except OSError:
            pass

    def test_cancel_event(self):
        ex = get_executor()
        ex.cancel_event("j1")
        self.assertFalse(ex.is_cancel_signaled("j1"))
        ex.signal_cancel("j1")
        self.assertTrue(ex.is_cancel_signaled("j1"))

    def test_concurrency_cap(self):
        ex = get_executor()
        ex.reset_for_tests(max_jobs=1)
        started = []
        gate = threading.Event()

        def block(**_):
            started.append(1)
            gate.wait(timeout=5)

        ok1 = ex.start_job("a", block, acquire_timeout=0.2)
        self.assertTrue(ok1)
        time.sleep(0.05)
        ok2 = ex.start_job("b", block, acquire_timeout=0.15)
        self.assertFalse(ok2)
        gate.set()
        time.sleep(0.15)
        # After finish, slot free
        done = []

        def quick(**_):
            done.append(1)

        ok3 = ex.start_job("c", quick, acquire_timeout=1.0)
        self.assertTrue(ok3)
        time.sleep(0.1)
        self.assertEqual(done, [1])

    def test_demo_uses_executor_and_cancel(self):
        agent = DemoAgent(duration=30, topic="cancel-me", conversation_id="c1")
        tid = agent.start()
        time.sleep(0.3)
        self.assertTrue(get_executor().is_live(tid) or agent.is_running())
        self.assertTrue(TaskRegistry().cancel_task(tid))
        deadline = time.time() + 5
        while time.time() < deadline:
            t = TaskRegistry().get_task(tid)
            if t and t.status in (TaskStatus.CANCELLED, TaskStatus.COMPLETED, TaskStatus.FAILED):
                break
            time.sleep(0.1)
        live = TaskRegistry().get_task(tid)
        self.assertIsNotNone(live)
        self.assertIn(live.status, (TaskStatus.CANCELLED, TaskStatus.COMPLETED, TaskStatus.FAILED))

    def test_orphan_reclaim(self):
        reg = TaskRegistry()
        task = reg.create_task("Ghost", "", task_type="demo")
        reg.update_task(task.id, TaskStatus.RUNNING)
        # No live thread → reclaim
        reclaimed = get_executor().reclaim_orphans(reg)
        self.assertIn(task.id, reclaimed)
        self.assertEqual(reg.get_task(task.id).status, TaskStatus.FAILED)


if __name__ == "__main__":
    unittest.main(verbosity=2)
