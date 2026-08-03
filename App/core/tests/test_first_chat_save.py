"""
Regression: the first TUI session never called start_new_conversation, so
/new discarded that chat. ensure + backfill + start_new must persist it.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


class FirstChatSaveTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.conv_dir = self._tmpdir.name

        # Isolate ConversationManager singleton + storage
        import knowledge.ConversationManager as cm

        self.cm = cm
        self._orig_dir = cm.CONVERSATIONS_DIR
        cm.CONVERSATIONS_DIR = self.conv_dir
        cm._manager = None
        cm.ConversationManager._instance = None

        # Reset live LLM messages to system-only
        import core.messageHandler as mh

        self.mh = mh
        with mh.messages_lock:
            mh.messages.clear()
            mh.messages.append({"role": "system", "content": "sys"})

        # Avoid network / task side effects
        self._patches = [
            patch("knowledge.ConversationManager.start_remote_conversation", create=True),
            patch("core.server_api.start_remote_conversation", return_value=None),
            patch("core.server_api.save_remote_conversation", return_value=None),
            patch("core.server_api.add_remote_message", return_value=True),
            patch("core.TaskManager.update_conversation_state", return_value=None),
        ]
        for p in self._patches:
            try:
                p.start()
            except Exception:
                pass

    def tearDown(self):
        for p in self._patches:
            try:
                p.stop()
            except Exception:
                pass
        self.cm.CONVERSATIONS_DIR = self._orig_dir
        self.cm._manager = None
        self.cm.ConversationManager._instance = None
        self._tmpdir.cleanup()

    def test_backfill_saves_orphan_llm_history_on_new(self):
        """Simulate first chat: messages in LLM list, no Conversation yet → /new saves."""
        # Orphan history (old bug state)
        with self.mh.messages_lock:
            self.mh.messages.append({"role": "user", "content": "USER: hello first chat"})
            self.mh.messages.append(
                {"role": "assistant", "content": "Hi — first session reply."}
            )

        mgr = self.cm.get_manager()
        self.assertIsNone(mgr.current_conversation)

        # /new path: start_new_conversation must save orphan first
        self.cm.start_new_conversation()

        files = [f for f in os.listdir(self.conv_dir) if f.endswith(".json") and f != "index.json"]
        self.assertEqual(len(files), 1, f"expected one saved chat, got {files}")

        data = json.loads(Path(self.conv_dir, files[0]).read_text(encoding="utf-8"))
        roles = [m["role"] for m in data["messages"]]
        self.assertIn("user", roles)
        self.assertIn("assistant", roles)
        self.assertTrue(
            any("first chat" in (m.get("content") or "") for m in data["messages"])
        )

        # New empty conversation is active
        self.assertIsNotNone(mgr.current_conversation)
        self.assertEqual(len(mgr.current_conversation.messages), 0)

    def test_add_message_auto_starts_conversation(self):
        """Going forward, first user message creates a ConversationManager thread."""
        with patch.object(self.mh, "main_runner") as runner:
            runner.is_set.return_value = True  # don't trigger response loop
            self.mh.add_message(content="USER: ping", role="user", trigger_response=False)

        mgr = self.cm.get_manager()
        self.assertIsNotNone(mgr.current_conversation)
        self.assertEqual(len(mgr.current_conversation.messages), 1)
        self.assertEqual(mgr.current_conversation.messages[0]["role"], "user")


if __name__ == "__main__":
    unittest.main()
