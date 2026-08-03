"""OpenCode integration unit tests (no live long coding run)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


class TestOpenCodeClient(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store_path = Path(self.tmp.name) / "sessions.json"
        import functions.system.opencode_client as oc

        self.oc = oc
        self._orig = oc.SESSIONS_PATH
        oc.SESSIONS_PATH = self.store_path
        oc._store = None

    def tearDown(self):
        self.oc.SESSIONS_PATH = self._orig
        self.oc._store = None
        self.tmp.cleanup()

    def test_session_store_upsert_and_find(self):
        store = self.oc.OpenCodeSessionStore(self.store_path)
        store.upsert(
            self.oc.OpenCodeSessionRecord(
                session_id="ses_test123",
                description="flask todo app",
                directory=str(Path.home() / "Desktop"),
                status="completed",
            )
        )
        got = store.get("ses_test123")
        self.assertIsNotNone(got)
        self.assertIn("flask", got.description)
        found = store.find_by_description("todo")
        self.assertTrue(any(s.session_id == "ses_test123" for s in found))

    def test_parse_events_extract_session_and_text(self):
        stdout = "\n".join(
            [
                json.dumps(
                    {
                        "type": "step_start",
                        "sessionID": "ses_abc",
                        "part": {"sessionID": "ses_abc"},
                    }
                ),
                json.dumps(
                    {
                        "type": "text",
                        "sessionID": "ses_abc",
                        "part": {"type": "text", "text": "Done building."},
                    }
                ),
            ]
        )
        events = self.oc._parse_json_events(stdout)
        self.assertEqual(self.oc._extract_session_id(events), "ses_abc")
        self.assertIn("Done", self.oc._extract_text_parts(events))

    def test_dispatch_knows_opencode(self):
        from core.tool_definitions import tools

        for t in tools:
            fn = t.get("function") or {}
            if fn.get("name") == "background_task":
                enums = (
                    fn.get("parameters", {})
                    .get("properties", {})
                    .get("task_type", {})
                    .get("enum", [])
                )
                self.assertIn("opencode", enums)
                self.assertIn("coding", enums)

    def test_opencode_agent_kind_and_tier(self):
        from core.TaskManager import OpenCodeAgent, infer_kind
        from core.jobs.model_router import infer_model_tier

        self.assertEqual(infer_kind("opencode"), "long")
        self.assertEqual(infer_model_tier("opencode"), "none")
        agent = OpenCodeAgent(objective="make hello world py", directory="desktop")
        self.assertEqual(agent.task.task_type, "opencode")
        self.assertEqual(agent.task.kind, "long")

    def test_title_only_still_gets_objective(self):
        """Main agent often sends title+session_id without objective — must not be empty."""
        from core.TaskManager import OpenCodeAgent

        agent = OpenCodeAgent(
            objective="",
            prompt="",
            title="Update test.py to hello world",
            session_id="ses_abc123",
            directory="desktop",
        )
        self.assertTrue(agent.objective.strip())
        self.assertIn("hello", agent.objective.lower())
        self.assertNotEqual(agent.objective.lower(), "opencode task")

    def test_resolve_opencode_objective_helper(self):
        from core.TaskManager import _resolve_opencode_objective

        self.assertEqual(
            _resolve_opencode_objective("", "OpenCode task", "rewrite file"),
            "rewrite file",
        )
        self.assertEqual(_resolve_opencode_objective("real work", "title"), "real work")

    def test_run_opencode_mocked(self):
        fake_out = (
            json.dumps(
                {
                    "type": "text",
                    "sessionID": "ses_mock1",
                    "part": {"type": "text", "text": "Created app.py"},
                }
            )
            + "\n"
        )

        class FakeProc:
            returncode = 0
            stdout = None
            stderr = None

            def __init__(self):
                self._lines = [fake_out, ""]
                self._i = 0

                class Out:
                    def __init__(self, parent):
                        self.parent = parent

                    def readline(self):
                        if self.parent._i < len(self.parent._lines):
                            v = self.parent._lines[self.parent._i]
                            self.parent._i += 1
                            return v
                        return ""

                    def read(self):
                        return ""

                class Err:
                    def read(self):
                        return ""

                self.stdout = Out(self)
                self.stderr = Err()

            def poll(self):
                return 0 if self._i >= len(self._lines) else None

            def terminate(self):
                pass

            def kill(self):
                pass

            def wait(self, timeout=None):
                return 0

        with patch.object(self.oc, "find_opencode_bin", return_value="opencode"):
            with patch("subprocess.Popen", return_value=FakeProc()):
                result = self.oc.run_opencode(
                    "create hello",
                    directory=self.tmp.name,
                    title="test",
                    description="mock session",
                    job_id="job1",
                    permissions="auto",
                )
        self.assertEqual(result.get("session_id"), "ses_mock1")
        self.assertIn("Created", result.get("summary") or result.get("content") or "")
        store = self.oc.get_session_store()
        self.assertIsNotNone(store.get("ses_mock1"))


if __name__ == "__main__":
    unittest.main()
