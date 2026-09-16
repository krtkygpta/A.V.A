"""
test_multi_client_hub.py - Comprehensive automated tests for:
1. Device Registry (presence, online/offline status, target resolution).
2. Tool search across device catalogs.
3. Multi-client conversation store isolation and cross-chat context.
4. Memory Dreamer detection of updated ongoing conversations.
5. Async tool execution RPC simulation.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER_DIR = ROOT / "Server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from services.device_registry import DeviceRegistry, device_registry
from services.conversation_store import ConversationStore, ConversationThread
from memorySystem.memoryDreamer import (
    collect_new_conversations,
    clean_conversation,
    compress_and_combine_small_chats,
    is_small_chat,
    is_large_or_important,
)


class MockWebSocket:
    """Simulates a connected client WebSocket."""
    def __init__(self, device_id: str):
        self.device_id = device_id
        self.sent_messages: list[str] = []
        self.closed = False

    async def send_text(self, text: str):
        self.sent_messages.append(text)
        # Auto-respond to tool_call
        data = json.loads(text)
        if data.get("type") == "tool_call":
            rpc_id = data.get("rpc_id")
            tool = data.get("tool")
            res = {"mock_result": f"Executed {tool} on {self.device_id}"}
            if tool == "get_location":
                res = {"city": "New Delhi", "latitude": 28.6139, "longitude": 77.2090}
            elif tool == "get_battery_status":
                res = {"level_percent": 88, "charging": True}
            device_registry.handle_rpc_response(rpc_id, result=res)


class TestMultiClientHub(unittest.TestCase):
    def setUp(self):
        self.test_dir = ROOT / "Server" / "data" / "test_convs"
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)
        self.test_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)

    def test_01_device_registry_presence_and_offline(self):
        reg = DeviceRegistry()
        mock_ws = MockWebSocket("client1_pc")

        # Register online PC
        reg.register(
            device_id="client1_pc",
            name="Workstation PC",
            device_type="windows",
            capabilities=["coding", "bash", "screenshot"],
            ws=mock_ws,
        )

        # Register second device and mark offline
        mock_phone_ws = MockWebSocket("client2_phone")
        reg.register(
            device_id="client2_phone",
            name="Pixel Phone",
            device_type="android",
            capabilities=["location", "battery", "ring"],
            ws=mock_phone_ws,
        )
        # Disconnect phone WebSocket
        reg.unregister_ws(mock_phone_ws)

        devices = reg.list_devices()
        dev_map = {d["device_id"]: d for d in devices}

        self.assertTrue(dev_map["client1_pc"]["online"])
        self.assertFalse(dev_map["client2_phone"]["online"])

        # Check presence summary contains both online and offline info
        summary = reg.get_presence_summary(caller_id="client1_pc")
        self.assertIn("client1_pc", summary)
        self.assertIn("Online, Caller", summary)
        self.assertIn("client2_phone", summary)
        self.assertIn("Offline", summary)

    def test_02_target_resolution(self):
        reg = DeviceRegistry()
        # Resolve caller
        dev_caller = reg.resolve_target("caller", caller_id="client1_pc")
        self.assertIsNotNone(dev_caller)
        self.assertEqual(dev_caller.device_id, "client1_pc")

        # Resolve alias 'phone'
        dev_phone = reg.resolve_target("phone")
        self.assertIsNotNone(dev_phone)
        self.assertEqual(dev_phone.device_id, "client2_phone")

        # Resolve alias 'pc'
        dev_pc = reg.resolve_target("pc")
        self.assertIsNotNone(dev_pc)
        self.assertEqual(dev_pc.device_id, "client1_pc")

    def test_03_tool_search_catalog(self):
        reg = DeviceRegistry()
        reg.register(
            device_id="client1_pc",
            name="Workstation",
            tool_catalog={
                "git_commit": {
                    "description": "Commit changes in the local git repository",
                    "parameters": {"message": {"type": "string"}},
                },
                "run_tests": {
                    "description": "Execute pytest suite on the local project",
                    "parameters": {},
                },
            },
        )

        results = reg.search_tools("git")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["tool"], "git_commit")

        results_tests = reg.search_tools("pytest")
        self.assertEqual(len(results_tests), 1)
        self.assertEqual(results_tests[0]["tool"], "run_tests")

    def test_04_multi_thread_conversation_store(self):
        store = ConversationStore(self.test_dir)

        # Client 1 (PC) starts a chat
        conv1_id = store.start(client_id="client1_pc", name="PC Coding Chat")
        store.add_message("user", "Help me debug a python file", client_id="client1_pc")
        store.add_message("assistant", "Sure, show me the code.", client_id="client1_pc")

        # Client 2 (Phone) starts a chat concurrently
        conv2_id = store.start(client_id="client2_phone", name="Phone Shopping Chat")
        store.add_message("user", "Find a good coffee shop", client_id="client2_phone")
        store.add_message("assistant", "There is Blue Tokai nearby.", client_id="client2_phone")

        self.assertNotEqual(conv1_id, conv2_id)

        # Verify threads persisted on disk
        t1 = store.get_thread(conv1_id)
        t2 = store.get_thread(conv2_id)
        self.assertEqual(len(t1["messages"]), 2)
        self.assertEqual(len(t2["messages"]), 2)

        # Verify recent_hour_context includes cross-chat summaries
        ctx = store.recent_hour_context()
        self.assertIn("PC Coding Chat", ctx)
        self.assertIn("Phone Shopping Chat", ctx)

    def test_05_async_tool_rpc_execution(self):
        async def _test():
            reg = DeviceRegistry()
            mock_ws = MockWebSocket("client3_web")
            reg.register(
                device_id="client3_web",
                name="Test Web Client",
                capabilities=["get_location", "get_battery_status"],
                ws=mock_ws,
            )

            # Invoke get_location via RPC
            res_loc = await reg.execute_tool_async("client3_web", "get_location", timeout=2.0)
            self.assertEqual(res_loc["status"], "success")
            self.assertEqual(res_loc["result"]["city"], "New Delhi")

            # Invoke get_battery_status via RPC
            res_bat = await reg.execute_tool_async("client3_web", "get_battery_status", timeout=2.0)
            self.assertEqual(res_bat["status"], "success")
            self.assertEqual(res_bat["result"]["level_percent"], 88)

        asyncio.run(_test())

    def test_06_is_small_and_large_chat_classification(self):
        from datetime import datetime
        now = datetime.now()

        # Small 2-turn simple chat
        small_chat = {
            "id": "conv_small_1",
            "created_at": "2026-09-12T10:00:00",
            "updated_at": "2026-09-12T10:00:05",
            "messages": [
                {"role": "user", "content": "What is the time?"},
                {"role": "assistant", "content": "It is 10:00 AM."},
            ],
        }
        self.assertFalse(is_large_or_important(small_chat))
        self.assertTrue(is_small_chat(small_chat, active_ids=set(), now_dt=now, min_inactive_seconds=60))

        # Large chat (6 messages)
        large_chat = {
            "id": "conv_large_1",
            "created_at": "2026-09-12T11:00:00",
            "updated_at": "2026-09-12T11:05:00",
            "messages": [
                {"role": "user", "content": "Let's build an app"},
                {"role": "assistant", "content": "What kind of app?"},
                {"role": "user", "content": "A task manager"},
                {"role": "assistant", "content": "Here is an outline"},
                {"role": "user", "content": "Add notifications"},
                {"role": "assistant", "content": "Notifications added"},
            ],
        }
        self.assertTrue(is_large_or_important(large_chat))
        self.assertFalse(is_small_chat(large_chat, active_ids=set(), now_dt=now, min_inactive_seconds=60))

        # Important chat with code block
        code_chat = {
            "id": "conv_code_1",
            "created_at": "2026-09-12T12:00:00",
            "updated_at": "2026-09-12T12:01:00",
            "messages": [
                {"role": "user", "content": "Write a python script"},
                {
                    "role": "assistant",
                    "content": "Here:\n```python\ndef hello():\n    print('Hello World')\n    return True\n```",
                },
            ],
        }
        self.assertTrue(is_large_or_important(code_chat))
        self.assertFalse(is_small_chat(code_chat, active_ids=set(), now_dt=now, min_inactive_seconds=60))

    def test_07_combine_and_compress_small_chats(self):
        import os
        from unittest.mock import patch

        store = ConversationStore(self.test_dir)

        # 1. Create three small chats from a past date (2026-09-12)
        small_1 = {
            "id": "20260912_090000_1111",
            "name": "What Is The Time",
            "created_at": "2026-09-12T09:00:00",
            "updated_at": "2026-09-12T09:00:05",
            "messages": [
                {"role": "user", "content": "What is the time?", "timestamp": "2026-09-12T09:00:00"},
                {"role": "assistant", "content": "It is 9:00 AM.", "timestamp": "2026-09-12T09:00:05"},
            ],
        }
        small_2 = {
            "id": "20260912_101500_2222",
            "name": "Turn On The Light",
            "created_at": "2026-09-12T10:15:00",
            "updated_at": "2026-09-12T10:15:04",
            "messages": [
                {"role": "user", "content": "Turn on living room light", "timestamp": "2026-09-12T10:15:00"},
                {"role": "assistant", "content": "Living room light is on.", "timestamp": "2026-09-12T10:15:04"},
            ],
        }
        small_3 = {
            "id": "20260912_143000_3333",
            "name": "Where Is My Phone",
            "created_at": "2026-09-12T14:30:00",
            "updated_at": "2026-09-12T14:30:08",
            "messages": [
                {"role": "user", "content": "Where is my phone?", "timestamp": "2026-09-12T14:30:00"},
                {"role": "assistant", "content": "Your phone is at home.", "timestamp": "2026-09-12T14:30:08"},
            ],
        }

        # 2. Create one large/important chat on the same date (should NOT be combined)
        large_1 = {
            "id": "20260912_160000_4444",
            "name": "Design AVA Architecture",
            "created_at": "2026-09-12T16:00:00",
            "updated_at": "2026-09-12T16:20:00",
            "messages": [
                {"role": "user", "content": "Let's review the architecture", "timestamp": "2026-09-12T16:00:00"},
                {"role": "assistant", "content": "We have Server, App, and Web Client.", "timestamp": "2026-09-12T16:01:00"},
                {"role": "user", "content": "How does websocket sync work?", "timestamp": "2026-09-12T16:05:00"},
                {"role": "assistant", "content": "Device registry handles RPC calls.", "timestamp": "2026-09-12T16:10:00"},
                {"role": "user", "content": "What about memory?", "timestamp": "2026-09-12T16:15:00"},
                {"role": "assistant", "content": "MemoryDreamer runs offline interval consolidation.", "timestamp": "2026-09-12T16:20:00"},
            ],
        }

        store.import_thread(small_1)
        store.import_thread(small_2)
        store.import_thread(small_3)
        store.import_thread(large_1)

        # Ensure all 4 were written
        self.assertEqual(len(list(self.test_dir.glob("*.json"))), 5)  # 4 threads + 1 index.json

        # Patch _CONVERSATIONS in memoryDreamer to point to self.test_dir
        with patch("memorySystem.memoryDreamer._CONVERSATIONS", self.test_dir):
            res = compress_and_combine_small_chats(conv_store=store, min_inactive_seconds=60)
            self.assertEqual(res["status"], "consolidated")
            self.assertEqual(res["combined_count"], 3)
            self.assertEqual(res["consolidated_threads"], 1)

        # Verify disk state:
        # Small chats are pruned
        self.assertFalse((self.test_dir / "20260912_090000_1111.json").exists())
        self.assertFalse((self.test_dir / "20260912_101500_2222.json").exists())
        self.assertFalse((self.test_dir / "20260912_143000_3333.json").exists())

        # Large chat is strictly preserved
        self.assertTrue((self.test_dir / "20260912_160000_4444.json").exists())

        # Consolidated thread exists
        consolidated_path = self.test_dir / "consolidated_20260912.json"
        self.assertTrue(consolidated_path.exists())

        cons_thread = store.get_thread("consolidated_20260912")
        self.assertIsNotNone(cons_thread)
        self.assertIn("Quick Queries & Actions", cons_thread["name"])
        self.assertEqual(len(cons_thread["messages"]), 6)  # 2 msgs * 3 chats
        self.assertIn("Quick actions", cons_thread["summary"])

        # Check that index was updated
        self.assertNotIn("20260912_090000_1111", store.index)
        self.assertIn("consolidated_20260912", store.index)
        self.assertIn("20260912_160000_4444", store.index)


if __name__ == "__main__":
    unittest.main()
