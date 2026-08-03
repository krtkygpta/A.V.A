"""
Manual-ish smoke flow for demo agent + timer (run without TUI).

From App/:
  python -m core.tests.smoke_demo_flow

What it does:
  1. Starts a 4s demo with ask=true → waits for needs_input → answers → DONE + inbox
  2. Starts a 1s timer → DONE, no inbox
  3. Prints inbox prompt text as conversation-start would see it
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import uuid
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import core.jobs.clarify as clarify_mod
import core.jobs.inbox as inbox_mod
import core.TaskManager as tm
from core.jobs.clarify import ClarificationBridge, get_clarification_bridge
from core.TaskManager import (
    DemoAgent,
    SubAgent,
    TaskRegistry,
    TaskStatus,
    TimerAgent,
    get_pending_clarifications_summary,
    get_pending_completions_summary,
    manage_background_tasks,
)


def _wait(task_id, pred, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        t = TaskRegistry().get_task(task_id)
        if t and pred(t):
            return t
        time.sleep(0.05)
    return TaskRegistry().get_task(task_id)


def main():
    # Isolate stores so smoke doesn't pollute real AVA data
    tmp = Path(tempfile.gettempdir()) / f"ava_smoke_{uuid.uuid4().hex}"
    tmp.mkdir(parents=True, exist_ok=True)
    inbox_mod.INBOX_STORE_PATH = tmp / "inbox.json"
    clarify_mod.CLARIFY_STORE_PATH = tmp / "clarifications.json"
    tm.TASK_STORE_PATH = tmp / "tasks.json"
    inbox_mod.Inbox._instance = None
    ClarificationBridge._instance = None
    TaskRegistry._instance = None
    ClarificationBridge._desktop_notify = staticmethod(lambda r: None)
    SubAgent._desktop_notify = staticmethod(lambda t: None)
    inbox_mod.get_inbox().clear()
    get_clarification_bridge().clear()

    print("=== 1) DEMO long + clarify ===")
    demo_id = DemoAgent(
        duration=4,
        topic="smoke vacation plan",
        ask=True,
        question="Beach or mountains?",
        require_user=True,
    ).start()
    print(f"  started demo id={demo_id}")
    t = _wait(demo_id, lambda x: x.status == TaskStatus.NEEDS_INPUT, timeout=8)
    assert t and t.status == TaskStatus.NEEDS_INPUT, t
    print(f"  needs_input request={t.pending_request_id}")
    print(f"  prompt would show:\n{get_pending_clarifications_summary() or '(none)'}")
    out = manage_background_tasks(
        action="answer",
        request_id=t.pending_request_id,
        answer="warm beaches under 1500",
        source="user",
    )
    print(f"  answer: {out}")
    t = _wait(demo_id, lambda x: x.status == TaskStatus.COMPLETED, timeout=12)
    assert t.status == TaskStatus.COMPLETED, t
    print(f"  result: {t.result}")
    print(f"  inbox unread: {inbox_mod.get_inbox().count_unread()}")

    print("\n=== 2) TIMER short (no sticky inbox) ===")
    before = inbox_mod.get_inbox().count_unread()
    timer_id = TimerAgent(duration_seconds=1, message="smoke timer done").start()
    print(f"  started timer id={timer_id}")
    t = _wait(timer_id, lambda x: x.status == TaskStatus.COMPLETED, timeout=5)
    assert t.status == TaskStatus.COMPLETED, t
    print(f"  timer result: {t.result} kind={t.kind}")
    after = inbox_mod.get_inbox().count_unread()
    assert after == before, f"timer leaked into inbox {before}->{after}"
    print("  inbox unchanged (timer not sticky) OK")

    print("\n=== 3) Conversation-start inject preview ===")
    print("INBOX:")
    print(get_pending_completions_summary() or "(none)")
    print("CLARIFICATIONS:")
    print(get_pending_clarifications_summary() or "(none)")

    print("\n=== ALL SMOKE CHECKS PASSED ===")
    print(f"(temp data: {tmp})")


if __name__ == "__main__":
    main()
