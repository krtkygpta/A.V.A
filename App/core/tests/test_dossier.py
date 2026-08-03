"""Tests for held task dossiers (origin pack + finish hold + retrieve)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

# App root on path
APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


class TestDossierCore(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.dossier_dir = self.tmp / "dossiers"
        self.dossier_dir.mkdir()

        import core.jobs.dossier as dossier_mod

        self.mod = dossier_mod
        self._orig_dir = dossier_mod.DOSSIERS_DIR
        dossier_mod.DOSSIERS_DIR = self.dossier_dir

    def tearDown(self):
        self.mod.DOSSIERS_DIR = self._orig_dir
        self._tmp.cleanup()

    def test_attach_and_finalize_holds_artifacts(self):
        from core.TaskManager import BackgroundTask, TaskStatus

        fake_messages = [
            {"role": "system", "content": "you are ava"},
            {"role": "user", "content": "build a flask todo app on desktop and run it later"},
            {"role": "assistant", "content": "I'll start a background job for that."},
        ]

        task = BackgroundTask(
            id="abcd1234",
            name="Delegate: flask todo",
            description="build flask todo",
            status=TaskStatus.RUNNING,
            conversation_id="conv1",
            conversation_name="coding chat",
            original_query="build a flask todo app",
            task_type="delegate",
            kind="long",
        )

        with patch.object(self.mod, "capture_origin_snapshot") as snap:
            snap.return_value = {
                "origin_turns": [
                    {"role": "user", "content": fake_messages[1]["content"]},
                    {"role": "assistant", "content": fake_messages[2]["content"]},
                ],
                "origin_user_request": fake_messages[1]["content"],
            }
            d0 = self.mod.attach_origin_to_task(task)

        self.assertEqual(d0.task_id, "abcd1234")
        self.assertIn("flask", d0.origin_user_request.lower())
        self.assertTrue((self.dossier_dir / "abcd1234.json").exists())

        task.status = TaskStatus.COMPLETED
        task.result = json.dumps(
            {
                "summary": "Created flask todo app",
                "report_path": str(self.tmp / "todo" / "app.py"),
            }
        )
        task.artifact_path = str(self.tmp / "todo" / "app.py")
        task.metadata["entry_point"] = str(self.tmp / "todo" / "app.py")
        task.metadata["run_command"] = f'python "{self.tmp / "todo" / "app.py"}"'

        d1 = self.mod.finalize_dossier(task)
        self.assertTrue(d1.held)
        self.assertTrue(d1.result_summary)
        self.assertTrue(d1.artifacts)
        self.assertTrue(any("Run:" in h or "python" in h.lower() for h in d1.continue_hints))

        text = self.mod.format_task_context("abcd1234", full=True)
        self.assertIn("flask", text.lower())
        self.assertIn("TASK DOSSIER", text)
        self.assertIn("Continue hints", text)

    def test_timer_main_action_is_result_only(self):
        from core.TaskManager import BackgroundTask, TaskStatus

        task = BackgroundTask(
            id="timer01",
            name="Timer (25s)",
            description="Timer done.",
            status=TaskStatus.COMPLETED,
            original_query="Timer done.",
            task_type="timer",
            kind="short",
            metadata={
                "follow_up_main_action": "WhatsApp Vinayak: hello",
            },
        )
        policy = self.mod.infer_announce_policy(task)
        self.assertEqual(policy, "result_only")
        lines = self.mod.speak_policy_lines(task)
        blob = "\n".join(lines).lower()
        self.assertIn("result_only", blob)
        self.assertIn("timer", blob)

    def test_enrich_clarification_includes_origin(self):
        from core.TaskManager import BackgroundTask, TaskStatus

        task = BackgroundTask(
            id="job99",
            name="Build app",
            description="build weather app",
            status=TaskStatus.NEEDS_INPUT,
            original_query="build weather app with dark mode",
            task_type="delegate",
            kind="long",
        )
        with patch.object(self.mod, "capture_origin_snapshot") as snap:
            snap.return_value = {
                "origin_turns": [
                    {"role": "user", "content": "make a weather app, dark theme preferred"},
                ],
                "origin_user_request": "make a weather app, dark theme preferred",
            }
            self.mod.attach_origin_to_task(task)

        ctx = self.mod.enrich_clarification_context(
            "job99", "Which API key should I use?"
        )
        self.assertIn("weather", ctx.lower())
        self.assertIn("dark", ctx.lower())

    def test_compact_done_includes_dossier_block(self):
        from core.TaskManager import BackgroundTask, TaskStatus

        task = BackgroundTask(
            id="res01",
            name="Research L3: keyboards",
            description="research",
            status=TaskStatus.COMPLETED,
            result=json.dumps(
                {
                    "summary": "Keyboards evolved from typewriters.",
                    "report_path": r"C:\Users\me\Desktop\AVA_Research\keyboards.md",
                    "level": 3,
                }
            ),
            original_query="history of keyboards",
            task_type="research",
            kind="long",
            conversation_name="research chat",
        )
        with patch.object(self.mod, "capture_origin_snapshot") as snap:
            snap.return_value = {
                "origin_turns": [
                    {"role": "user", "content": "deep research history of keyboards"},
                ],
                "origin_user_request": "deep research history of keyboards",
            }
            self.mod.attach_origin_to_task(task)
        self.mod.finalize_dossier(task)

        done = task.to_compact_done()
        self.assertIn("ORIGIN", done)
        self.assertIn("keyboards", done.lower())
        self.assertIn("Announce policy", done)


class TestManageContextAction(unittest.TestCase):
    def test_manage_context_action_exists(self):
        from core.tool_definitions import tools

        names = []
        for t in tools:
            fn = (t.get("function") or {})
            if fn.get("name") == "manage_background_tasks":
                enums = (
                    fn.get("parameters", {})
                    .get("properties", {})
                    .get("action", {})
                    .get("enum", [])
                )
                names = enums
        self.assertIn("context", names)
        self.assertIn("dossier", names)


if __name__ == "__main__":
    unittest.main()
