"""Tests for consecutive background tasks (follow_up chain)."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import core.TaskManager as tm
from core.TaskManager import (
    TaskRegistry,
    TimerAgent,
    dispatch_background_task,
    parse_follow_up_specs,
    spawn_follow_up_tasks,
    TaskStatus,
)

_ORIG = tm.TASK_STORE_PATH
_TEST_STORE = Path(tempfile.gettempdir()) / f"ava_followup_{os.getpid()}.json"


def setUpModule():
    tm.TASK_STORE_PATH = _TEST_STORE
    TaskRegistry._instance = None
    try:
        _TEST_STORE.unlink()
    except OSError:
        pass


def tearDownModule():
    tm.TASK_STORE_PATH = _ORIG
    TaskRegistry._instance = None
    try:
        _TEST_STORE.unlink()
    except OSError:
        pass


class TestParseFollowUp(unittest.TestCase):
    def test_dict_and_list_and_json(self):
        self.assertEqual(len(parse_follow_up_specs({"task_type": "research", "topic": "x"})), 1)
        self.assertEqual(
            len(
                parse_follow_up_specs(
                    [
                        {"task_type": "research", "topic": "a"},
                        {"task_type": "timer", "duration": 1},
                    ]
                )
            ),
            2,
        )
        self.assertEqual(
            parse_follow_up_specs('{"task_type":"research","topic":"keyboards"}')[0][
                "topic"
            ],
            "keyboards",
        )
        self.assertEqual(parse_follow_up_specs(""), [])
        self.assertEqual(parse_follow_up_specs("not-json"), [])


class TestFollowUpSpawn(unittest.TestCase):
    def setUp(self):
        TaskRegistry._instance = None
        try:
            _TEST_STORE.unlink()
        except OSError:
            pass

    def test_timer_main_action_no_delegate(self):
        """Small post-timer work should queue main_action, not spawn a worker."""
        tid, msg = dispatch_background_task(
            "timer",
            duration=1,
            message="Timer done",
            alert_mode="notify",
            follow_up=json.dumps(
                {
                    "task_type": "main_action",
                    "objective": "WhatsApp Mein Hi Hoon the current time",
                }
            ),
            conversation_id="conv-main",
            conversation_name="Test",
        )
        self.assertTrue(tid)
        self.assertIn("auto-start", msg.lower())

        reg = TaskRegistry()
        deadline = time.time() + 8
        while time.time() < deadline:
            parent = reg.get_task(tid)
            if parent and parent.status == TaskStatus.COMPLETED:
                if parent.metadata.get("follow_up_main_action"):
                    break
            time.sleep(0.15)

        parent = reg.get_task(tid)
        self.assertEqual(parent.status, TaskStatus.COMPLETED)
        self.assertIn("WhatsApp", parent.metadata.get("follow_up_main_action", ""))
        ids = parent.metadata.get("follow_up_spawned_ids") or []
        self.assertTrue(any(str(x).startswith("main:") for x in ids))
        # No real background child task registered as a demo/delegate
        for x in ids:
            if not str(x).startswith("main:"):
                child = reg.get_task(x)
                self.assertIsNone(child)

    def test_timer_spawns_demo_follow_up(self):
        # Short timer with follow_up demo (no network)
        tid, msg = dispatch_background_task(
            "timer",
            duration=1,
            message="Timer done",
            alert_mode="notify",
            follow_up='{"task_type":"demo","duration":2,"topic":"after-timer"}',
            conversation_id="conv-test",
            conversation_name="Test",
        )
        self.assertTrue(tid)
        self.assertIn("auto-start", msg.lower())

        reg = TaskRegistry()
        parent = reg.get_task(tid)
        self.assertIsNotNone(parent)
        self.assertTrue(parent.metadata.get("follow_up"))

        # Wait for timer + follow_up routing
        deadline = time.time() + 8
        child_ids = []
        while time.time() < deadline:
            parent = reg.get_task(tid)
            if parent and parent.status == TaskStatus.COMPLETED:
                child_ids = list(parent.metadata.get("follow_up_spawned_ids") or [])
                if child_ids:
                    break
            # Manual spawn if notify path not polled — completion runs in timer thread
            time.sleep(0.2)

        parent = reg.get_task(tid)
        self.assertEqual(parent.status, TaskStatus.COMPLETED)
        child_ids = list(parent.metadata.get("follow_up_spawned_ids") or [])
        self.assertTrue(child_ids, "expected follow_up child id on completed timer")
        child = reg.get_task(child_ids[0])
        self.assertIsNotNone(child)
        self.assertEqual(child.task_type, "demo")
        # Child should not re-fire the same follow_up infinitely
        self.assertEqual(int(child.metadata.get("follow_up_depth") or 0), 1)


if __name__ == "__main__":
    unittest.main()
