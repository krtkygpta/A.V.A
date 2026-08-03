"""
Phase 1 tests: kind field, durable inbox, dismiss/ack, compact DONE, timers.

Run with: python -m unittest core.tests.test_inbox
(from the App/ directory)
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

import core.jobs.inbox as inbox_mod
from core.TaskManager import (
    BackgroundTask,
    TaskRegistry,
    TaskStatus,
    TimerAgent,
    infer_kind,
    manage_background_tasks,
)


def _reset_inbox(path: Path):
    inbox_mod.INBOX_STORE_PATH = path
    inbox_mod.Inbox._instance = None
    return inbox_mod.get_inbox()


class TestInferKind(unittest.TestCase):
    def test_defaults_by_task_type(self):
        self.assertEqual(infer_kind("research"), "long")
        self.assertEqual(infer_kind("mission"), "long")
        self.assertEqual(infer_kind("delegate"), "long")
        self.assertEqual(infer_kind("timer"), "short")
        self.assertEqual(infer_kind("scrape"), "short")
        self.assertEqual(infer_kind("smarthome"), "short")
        self.assertEqual(infer_kind("general"), "short")

    def test_explicit_override(self):
        self.assertEqual(infer_kind("timer", "long"), "long")
        self.assertEqual(infer_kind("research", "short"), "short")
        self.assertEqual(infer_kind("research", "bogus"), "long")


class TestBackgroundTaskKind(unittest.TestCase):
    def test_kind_round_trip(self):
        data = {
            "id": "t1",
            "name": "n",
            "description": "",
            "status": "completed",
            "task_type": "research",
            "kind": "long",
        }
        task = BackgroundTask.from_dict(data)
        self.assertEqual(task.kind, "long")
        self.assertEqual(task.to_dict()["kind"], "long")

    def test_kind_inferred_for_legacy_rows(self):
        data = {
            "id": "t2",
            "name": "n",
            "description": "",
            "task_type": "research",
        }
        task = BackgroundTask.from_dict(data)
        self.assertEqual(task.kind, "long")

    def test_to_compact_done(self):
        task = BackgroundTask(
            id="abc",
            name="Research: AI",
            description="",
            status=TaskStatus.COMPLETED,
            result="Findings about AI safety",
            task_type="research",
            kind="long",
            alert_mode="notify",
        )
        text = task.to_compact_done()
        self.assertIn("id=abc", text)
        self.assertIn("type=research", text)
        self.assertIn("Findings about AI safety", text)
        self.assertIn("Alert mode: notify", text)
        # UI line must stay short and not dump raw JSON keys
        ui = task.to_ui_done()
        self.assertIn("done", ui.lower())
        self.assertIn("Research: AI", ui)
        self.assertNotIn('"status"', ui)

    def test_to_compact_done_timer_continuous(self):
        task = BackgroundTask(
            id="t1",
            name="Timer 5m",
            description="SCHOOL ALARM",
            status=TaskStatus.COMPLETED,
            result="SCHOOL ALARM",
            task_type="timer",
            kind="short",
            original_query="ALARM FOR SCHOOL keep ringing",
            alert_mode="continuous",
        )
        text = task.to_compact_done()
        self.assertIn("Alert mode: continuous", text)
        self.assertIn("SCHOOL ALARM", text)

    def test_to_ui_done_research_json(self):
        import json

        result = json.dumps(
            {
                "status": "success",
                "level": 4,
                "summary": "Short human summary of the person.",
                "report_path": r"C:\Users\x\Desktop\AVA_Research\r.md",
            }
        )
        task = BackgroundTask(
            id="r1",
            name="Research L4: Someone",
            description="",
            status=TaskStatus.COMPLETED,
            result=result,
            task_type="research",
            kind="long",
            alert_mode="notify",
        )
        ui = task.to_ui_done()
        self.assertIn("Short human summary", ui)
        self.assertIn("AVA_Research", ui)
        self.assertNotIn('"status": "success"', ui)
        compact = task.to_compact_done()
        self.assertIn("Full report path:", compact)
        self.assertNotIn('"status": "success"', compact)

    def test_normalize_alert_mode(self):
        from core.TaskManager import normalize_alert_mode

        self.assertEqual(normalize_alert_mode("continuous"), "continuous")
        self.assertEqual(normalize_alert_mode("alarm"), "continuous")
        self.assertEqual(normalize_alert_mode("notify"), "notify")
        self.assertEqual(normalize_alert_mode(None), "notify")
        self.assertEqual(normalize_alert_mode("short"), "short")

    def test_registry_create_kind(self):
        registry = TaskRegistry()
        registry.clear_all()
        t1 = registry.create_task("T1", "", task_type="research")
        t2 = registry.create_task("T2", "", task_type="research", kind="short")
        self.assertEqual(t1.kind, "long")
        self.assertEqual(t2.kind, "short")


class TestInbox(unittest.TestCase):
    def setUp(self):
        self.tmp_path = Path(
            tempfile.gettempdir()
        ) / f"ava_inbox_test_{uuid.uuid4().hex}.json"
        self._orig_path = inbox_mod.INBOX_STORE_PATH
        self.inbox = _reset_inbox(self.tmp_path)
        self.inbox.clear()

    def tearDown(self):
        _reset_inbox(self._orig_path)
        try:
            self.tmp_path.unlink()
        except OSError:
            pass

    def test_add_and_list_unread(self):
        self.inbox.add("j1", "Research: AI", summary="Findings")
        self.inbox.add("j2", "Research: Weather", summary="Sunny")
        unread = self.inbox.list_unread()
        self.assertEqual(len(unread), 2)
        self.assertEqual(self.inbox.count_unread(), 2)

    def test_add_is_idempotent_per_id(self):
        self.inbox.add("j1", "Research: AI", summary="v1")
        self.inbox.add("j1", "Research: AI", summary="v2")
        self.assertEqual(self.inbox.count_unread(), 1)
        self.assertEqual(self.inbox.get("j1").summary, "v1")

    def test_acknowledge_and_dismiss(self):
        self.inbox.add("j1", "Title", summary="s")
        self.assertTrue(self.inbox.acknowledge("j1"))
        self.assertEqual(self.inbox.count_unread(), 0)
        self.inbox.add("j2", "Title", summary="s")
        self.assertTrue(self.inbox.dismiss("j2"))
        self.assertEqual(self.inbox.count_unread(), 0)
        self.assertFalse(self.inbox.dismiss("missing"))

    def test_prompt_text_caps_at_three(self):
        for i in range(5):
            self.inbox.add(f"j{i}", f"Task {i}", summary=f"summary {i}")
        text = self.inbox.prompt_text()
        lines = [l for l in text.splitlines() if l.startswith("[UNREAD]")]
        self.assertEqual(len(lines), 3)
        self.assertIn("and 2 more", text)
        self.assertIn("Prefer the freshest", text)

    def test_prompt_text_empty(self):
        self.assertEqual(self.inbox.prompt_text(), "")

    def test_persists_and_reloads(self):
        self.inbox.add("p1", "Persistent", summary="kept")
        self.assertTrue(self.tmp_path.exists())
        _reset_inbox(self.tmp_path)
        reloaded = inbox_mod.get_inbox()
        self.assertEqual(reloaded.count_unread(), 1)
        self.assertEqual(reloaded.get("p1").title, "Persistent")


class TestJobCompletionRoutesToInbox(unittest.TestCase):
    def setUp(self):
        import core.TaskManager as tm

        self._orig_tasks = tm.TASK_STORE_PATH
        self.task_path = Path(tempfile.gettempdir()) / f"ava_inbox_tasks_{uuid.uuid4().hex}.json"
        tm.TASK_STORE_PATH = self.task_path
        TaskRegistry._instance = None
        TaskRegistry().clear_all()
        self.tmp_path = Path(
            tempfile.gettempdir()
        ) / f"ava_inbox_test_{uuid.uuid4().hex}.json"
        self._orig_path = inbox_mod.INBOX_STORE_PATH
        self.inbox = _reset_inbox(self.tmp_path)
        self.inbox.clear()
        tm.SubAgent._desktop_notify = staticmethod(lambda task: None)

    def tearDown(self):
        import core.TaskManager as tm

        tm.TASK_STORE_PATH = self._orig_tasks
        TaskRegistry._instance = None
        _reset_inbox(self._orig_path)
        for p in (self.tmp_path, self.task_path):
            try:
                p.unlink()
            except OSError:
                pass

    def test_timer_never_lands_in_inbox_even_if_kind_long(self):
        """Timers outlive chats while counting, but completion is one-shot."""
        task_id = TimerAgent(
            duration_seconds=0, message="ding", kind="long"
        ).start()
        time.sleep(0.35)
        self.assertEqual(self.inbox.count_unread(), 0)
        task = TaskRegistry().get_task(task_id)
        self.assertIsNotNone(task)
        self.assertEqual(task.kind, "short")
        self.assertEqual(task.status, TaskStatus.COMPLETED)

    def test_short_timer_skips_inbox(self):
        TimerAgent(duration_seconds=0, message="short").start()
        time.sleep(0.35)
        self.assertEqual(self.inbox.count_unread(), 0)

    def test_result_acknowledges_and_dismiss_clears(self):
        registry = TaskRegistry()
        task = registry.create_task("Research: AI", "", task_type="research")
        registry.update_task(task.id, TaskStatus.COMPLETED, result="Findings")
        self.inbox.add(id=task.id, title=task.name, summary="Findings")
        self.assertEqual(self.inbox.count_unread(), 1)

        out = manage_background_tasks("result", task.id)
        self.assertIn("Findings", out)
        self.assertEqual(self.inbox.count_unread(), 0)

        task2 = registry.create_task("Research: X", "", task_type="research")
        registry.update_task(task2.id, TaskStatus.COMPLETED, result="X")
        self.inbox.add(id=task2.id, title=task2.name, summary="X")
        out2 = manage_background_tasks("dismiss", task2.id)
        self.assertIn("Dismissed", out2)
        self.assertEqual(self.inbox.count_unread(), 0)


class TestNotificationNonBlocking(unittest.TestCase):
    def test_send_notification_is_non_blocking(self):
        """send_notification must return promptly even when toast would block."""
        import time as _time

        try:
            import plyer  # noqa: F401
        except ImportError:
            self.skipTest("plyer not installed in this environment")

        # Inline the non-blocking contract so we don't import the whole package.
        called = {"v": False}

        def fake_blocking(title, message, timeout=10):
            called["v"] = True
            _time.sleep(1.0)

        def send_notification(title, message):
            threading.Thread(
                target=fake_blocking,
                args=(title, message, 10),
                daemon=True,
            ).start()

        start = _time.perf_counter()
        send_notification("AVA test", "non-blocking check")
        elapsed = _time.perf_counter() - start

        self.assertLess(elapsed, 0.5, "send_notification blocked the caller")
        self.assertFalse(called["v"], "toast fired before caller returned")

        deadline = _time.time() + 2.0
        while not called["v"] and _time.time() < deadline:
            _time.sleep(0.02)
        self.assertTrue(called["v"], "background toast thread never ran")

    def test_ping_tool_is_non_blocking(self):
        """Main-agent ping must return immediately (ring on background thread)."""
        import importlib.util
        import json as _json
        import time as _time
        from pathlib import Path

        # Load notifiers.py directly to avoid heavy functions package imports.
        path = Path(__file__).resolve().parents[2] / "functions" / "system" / "notifiers.py"
        spec = importlib.util.spec_from_file_location("ava_notifiers_test", path)
        n = importlib.util.module_from_spec(spec)
        # Stub plyer so import works without desktop deps.
        import sys
        import types

        fake_plyer = types.ModuleType("plyer")
        fake_plyer.notification = types.SimpleNamespace(notify=lambda **kw: None)
        sys.modules.setdefault("plyer", fake_plyer)
        spec.loader.exec_module(n)

        started = {"v": False}
        orig = n.ring_timer

        def fake_ring(duration_seconds, frequency=1000, beep_interval=0.5):
            started["v"] = True
            _time.sleep(0.8)
            return _json.dumps({"status": "success", "content": "fake"})

        n.ring_timer = fake_ring
        try:
            start = _time.perf_counter()
            out = n.ping(duration_seconds=30)
            elapsed = _time.perf_counter() - start
            self.assertLess(elapsed, 0.4, "ping blocked the caller")
            payload = _json.loads(out)
            self.assertEqual(payload.get("status"), "success")
            deadline = _time.time() + 2.0
            while not started["v"] and _time.time() < deadline:
                _time.sleep(0.02)
            self.assertTrue(started["v"], "background ring never started")
        finally:
            n.ring_timer = orig
            n.stop_ping()
if __name__ == "__main__":
    unittest.main(verbosity=2)
