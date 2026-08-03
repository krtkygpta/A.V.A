"""Phase 5: services host, EventSink → inbox, health/call stubs."""

import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import core.jobs.inbox as inbox_mod
from core.jobs.notify import route_service_event, flush_pending_announcements
from core.services.base import EventSink, ServiceEvent
from core.services.call_manager import CallManager
from core.services.health_monitor import HealthMonitor
from core.services.registry import ServiceRegistry, get_service_registry
from core.TaskManager import (
    CompletionQueue,
    ConversationStateManager,
    TaskRegistry,
    TaskStatus,
    BackgroundTask,
    TaskPriority,
)
import core.TaskManager as tm


class TestServiceEvents(unittest.TestCase):
    def setUp(self):
        self.inbox_path = Path(tempfile.gettempdir()) / f"ava_svc_inbox_{uuid.uuid4().hex}.json"
        self._orig_inbox = inbox_mod.INBOX_STORE_PATH
        inbox_mod.INBOX_STORE_PATH = self.inbox_path
        inbox_mod.Inbox._instance = None
        inbox_mod.get_inbox().clear()

        self.task_path = Path(tempfile.gettempdir()) / f"ava_svc_tasks_{uuid.uuid4().hex}.json"
        self._orig_tasks = tm.TASK_STORE_PATH
        tm.TASK_STORE_PATH = self.task_path
        TaskRegistry._instance = None
        TaskRegistry().clear_all()

    def tearDown(self):
        inbox_mod.INBOX_STORE_PATH = self._orig_inbox
        inbox_mod.Inbox._instance = None
        tm.TASK_STORE_PATH = self._orig_tasks
        TaskRegistry._instance = None
        for p in (self.inbox_path, self.task_path):
            try:
                p.unlink()
            except OSError:
                pass

    def test_route_creates_inbox(self):
        with patch("core.jobs.notify.desktop_notify"):
            route_service_event(
                ServiceEvent(
                    service="health_monitor",
                    event_type="hr_alert",
                    title="Heart rate high",
                    summary="HR 160 ≥ 140",
                    severity="critical",
                    create_inbox=True,
                    speak_now=False,
                    id="hr1",
                )
            )
        unread = inbox_mod.get_inbox().list_unread()
        self.assertEqual(len(unread), 1)
        self.assertEqual(unread[0].id, "hr1")
        self.assertEqual(unread[0].source, "service")

    def test_call_manager_emit(self):
        with patch("core.jobs.notify.desktop_notify"):
            cm = CallManager()
            cm.start(EventSink(), __import__("threading").Event())
            cm.emit_call_needs_user("Incoming call from Mom")
        self.assertGreaterEqual(inbox_mod.get_inbox().count_unread(), 1)

    def test_health_monitor_test_alert(self):
        with patch("core.jobs.notify.desktop_notify"):
            hm = HealthMonitor(threshold=140)
            hm.start(EventSink(), __import__("threading").Event())
            hm.emit_test_alert(170)
            hm.stop()
        items = [i for i in inbox_mod.get_inbox().list_unread() if "Heart" in i.title]
        self.assertTrue(items)

    def test_registry_start_stop(self):
        os.environ["AVA_SERVICES"] = "call_manager"
        reg = get_service_registry()
        reg.reset_for_tests()
        started = reg.start_all()
        self.assertIn("call_manager", started)
        self.assertIn("call_manager", reg.list_running())
        reg.stop_all()
        self.assertEqual(reg.list_running(), [])
        os.environ.pop("AVA_SERVICES", None)

    def test_flush_pending_announcements(self):
        """Jobs finished between chats should inject DONE on flush."""
        reg = TaskRegistry()
        task = reg.create_task(
            "Demo done",
            "",
            task_type="demo",
            kind="long",
        )
        reg.update_task(task.id, TaskStatus.COMPLETED, result="TEST DONE SUCCESFULL")
        live = reg.get_task(task.id)

        cq = CompletionQueue()
        # clear queues
        while cq.pop(timeout=0.01):
            pass
        cq._idle_notifications.clear()
        cq._deferred_notifications.clear()
        cq.store_for_idle(live)

        state = ConversationStateManager()
        state.conversation_started("conv_f", "Flush Test")
        state.is_conversation_active = True

        injected = []

        def fake_add_message(**kwargs):
            injected.append(kwargs)

        with patch("core.messageHandler.add_message", side_effect=fake_add_message):
            n = flush_pending_announcements(trigger_response=True)

        self.assertEqual(n, 1)
        self.assertTrue(any("BACKGROUND TASK DONE" in str(m.get("content", "")) for m in injected))
        self.assertTrue(any(m.get("trigger_response") for m in injected))
        self.assertIsNotNone(reg.get_task(task.id).notified_at)


if __name__ == "__main__":
    unittest.main(verbosity=2)
