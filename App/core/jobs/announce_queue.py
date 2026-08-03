"""
announce_queue.py - Message-queue for background task DONE announcements.

Policy (user-facing):
  - If the main agent is *busy* (generating / tool loop): hold completions and
    deliver them **one by one** after free (each gets its own LLM turn).
  - If the main agent is *idle*: deliver all pending completions as a **batch**
    in a single turn (one wake, multiple [BACKGROUND TASK DONE] injects).

Desktop toasts still fire at completion time (TaskManager); this queue only
serializes *spoken / chat* delivery so concurrent timer + research finishes
do not stomp each other.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Deque, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from core.TaskManager import BackgroundTask


@dataclass
class AnnounceItem:
    task: "BackgroundTask"
    enqueued_at: datetime = field(default_factory=datetime.now)
    # "speak" = inject + wake main; "context_only" reserved
    mode: str = "speak"
    # True if alert_for_completed_task already ran (e.g. IMMEDIATE timer ring)
    already_alerted: bool = False


class AnnounceQueue:
    """Singleton FIFO for main-agent completion announcements."""

    _instance = None
    _create_lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._create_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if getattr(self, "_initialized", False):
            return
        self._lock = threading.Lock()
        self._pending: Deque[AnnounceItem] = deque()
        self._ids: set[str] = set()
        # Ids already delivered this process — permanent dedupe for re-routes
        self._announced_ids: set[str] = set()
        # Once the main agent has been busy while items waited, drain serially
        # until the queue is empty again.
        self._serial_mode: bool = False
        self._main_busy: bool = False
        self._initialized = True

    # ── lifecycle ──────────────────────────────────────────────────────────

    def mark_main_busy(self) -> None:
        with self._lock:
            self._main_busy = True
            if self._pending:
                self._serial_mode = True

    def mark_main_idle(self) -> None:
        with self._lock:
            self._main_busy = False
            if not self._pending:
                self._serial_mode = False

    def is_main_busy(self) -> bool:
        with self._lock:
            return self._main_busy

    # ── enqueue ────────────────────────────────────────────────────────────

    def enqueue(
        self,
        task: "BackgroundTask",
        *,
        mode: str = "speak",
        already_alerted: bool = False,
    ) -> bool:
        """
        Queue a completed task for spoken/chat delivery.
        Returns False if already queued, already notified, or fully handled.
        """
        tid = str(getattr(task, "id", "") or id(task))
        # Never re-announce jobs the user/main already heard
        try:
            meta = getattr(task, "metadata", None) or {}
            if meta.get("handled") or meta.get("dismissed") or meta.get("announced"):
                return False
            if getattr(task, "notified_at", None) is not None:
                return False
        except Exception:
            pass
        with self._lock:
            if tid in self._ids:
                return False
            # Remember announced ids even after pop so mid-turn re-routes cannot
            # re-queue the same completion (triple-speak bug).
            if tid in getattr(self, "_announced_ids", set()):
                return False
            self._ids.add(tid)
            self._pending.append(
                AnnounceItem(
                    task=task, mode=mode, already_alerted=already_alerted
                )
            )
            # Arrived while main is mid-turn → force serial drain afterward
            if self._main_busy:
                self._serial_mode = True
            return True

    def has_pending(self) -> bool:
        with self._lock:
            return bool(self._pending)

    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    def in_serial_mode(self) -> bool:
        with self._lock:
            return self._serial_mode

    # ── dequeue ────────────────────────────────────────────────────────────

    def pop_delivery_batch(self) -> List[AnnounceItem]:
        """
        Return the next batch of AnnounceItems to inject into the chat.

        - serial_mode → at most 1 item
        - else → all pending items (batch)

        Empty if main is still marked busy (caller should not inject mid-turn).
        """
        with self._lock:
            if self._main_busy or not self._pending:
                return []

            if self._serial_mode:
                item = self._pending.popleft()
                tid = str(getattr(item.task, "id", "") or id(item.task))
                self._ids.discard(tid)
                self._announced_ids.add(tid)
                if not self._pending:
                    self._serial_mode = False
                return [item]

            # Batch mode: drain everything
            batch = list(self._pending)
            for item in batch:
                tid = str(getattr(item.task, "id", "") or id(item.task))
                self._announced_ids.add(tid)
            self._pending.clear()
            self._ids.clear()
            self._serial_mode = False
            return batch

    def mark_announced(self, task_id: str) -> None:
        """Record that this task was spoken — blocks future enqueue."""
        tid = str(task_id or "").strip()
        if not tid:
            return
        with self._lock:
            self._announced_ids.add(tid)
            self._ids.discard(tid)

    def was_announced(self, task_id: str) -> bool:
        tid = str(task_id or "").strip()
        with self._lock:
            return tid in self._announced_ids

    def clear(self) -> None:
        with self._lock:
            self._pending.clear()
            self._ids.clear()
            # keep _announced_ids — process lifetime memory of what we already said
            self._serial_mode = False


def get_announce_queue() -> AnnounceQueue:
    return AnnounceQueue()
