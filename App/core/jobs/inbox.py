"""
inbox.py - Durable unread-result inbox for long background jobs.

A long job (kind == "long") that finishes lands a compact item here as
``unread``. Every new conversation injects the top few unread items into the
system prompt; they keep showing until the user asks about them (acknowledged
via ``result``) or dismisses them. Short jobs never touch this store.

Persistence: App/data/tasks/inbox.json (JSON is fine for v1).
"""

import json
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional

INBOX_STORE_PATH = (
    Path(__file__).resolve().parent.parent.parent / "data" / "tasks" / "inbox.json"
)

SEVERITY_ORDER = {"critical": 0, "important": 1, "normal": 2, "low": 3}


@dataclass
class InboxItem:
    id: str
    source: str = "job"  # job | service
    title: str = ""
    summary: str = ""
    artifact_path: Optional[str] = None
    severity: str = "normal"  # critical | important | normal | low
    status: str = "unread"  # unread | acknowledged
    created_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "source": self.source,
            "title": self.title,
            "summary": self.summary,
            "artifact_path": self.artifact_path,
            "severity": self.severity,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "InboxItem":
        return cls(
            id=data.get("id", ""),
            source=data.get("source", "job"),
            title=data.get("title", ""),
            summary=data.get("summary", ""),
            artifact_path=data.get("artifact_path"),
            severity=data.get("severity", "normal"),
            status=data.get("status", "unread"),
            created_at=datetime.fromisoformat(
                data.get("created_at", datetime.now().isoformat())
            ),
        )


class Inbox:
    """Singleton durable store of unread/acknowledged result pointers."""

    _instance = None
    _lock = threading.RLock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._items: dict = {}
        self._load()
        self._initialized = True

    def _load(self):
        try:
            if not INBOX_STORE_PATH.exists():
                return
            data = json.loads(INBOX_STORE_PATH.read_text(encoding="utf-8"))
            for raw in data.get("items", []):
                item = InboxItem.from_dict(raw)
                self._items[item.id] = item
        except Exception as e:
            print(f"[Inbox] Could not load inbox store: {e}")

    def _persist(self):
        try:
            INBOX_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "updated_at": datetime.now().isoformat(),
                "items": [item.to_dict() for item in self._items.values()],
            }
            INBOX_STORE_PATH.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as e:
            print(f"[Inbox] Could not save inbox store: {e}")

    def add(self, id: str, title: str, summary: str = "",
            artifact_path: Optional[str] = None, severity: str = "normal",
            source: str = "job") -> InboxItem:
        """Create an unread item. Idempotent per id (long jobs re-run kept once)."""
        with self._lock:
            existing = self._items.get(id)
            if existing:
                return existing
            item = InboxItem(
                id=id,
                source=source,
                title=title,
                summary=summary,
                artifact_path=artifact_path,
                severity=severity,
            )
            self._items[id] = item
            self._persist()
            return item

    def list_unread(self) -> List[InboxItem]:
        with self._lock:
            unread = [it for it in self._items.values() if it.status == "unread"]
            # critical first, then newest
            unread.sort(
                key=lambda it: (
                    SEVERITY_ORDER.get(it.severity, 9),
                    -(it.created_at.timestamp() if it.created_at else 0),
                )
            )
            return unread

    def count_unread(self) -> int:
        with self._lock:
            return sum(1 for it in self._items.values() if it.status == "unread")

    def get(self, item_id: str) -> Optional[InboxItem]:
        with self._lock:
            return self._items.get(item_id)

    def acknowledge(self, item_id: str) -> bool:
        return self._set_status(item_id, "acknowledged")

    def dismiss(self, item_id: str) -> bool:
        return self._set_status(item_id, "acknowledged")

    def _set_status(self, item_id: str, status: str) -> bool:
        with self._lock:
            item = self._items.get(item_id)
            if not item:
                return False
            item.status = status
            self._persist()
            return True

    def prompt_text(self, max_items: int = 3) -> str:
        """Compact one-liner list for the system prompt (top N + count).

        Newest unread first (after severity). Includes rough age so the main
        agent can prefer fresh completions over day-old research.
        """
        unread = self.list_unread()
        if not unread:
            return ""
        now = datetime.now()
        lines = [
            "Unread long-job results (passive context only — do NOT monologue these "
            "on open/greeting; wait for the user to ask or for a [BACKGROUND TASK DONE] inject). "
            "Newest/important first. If user says ignore → dismiss that id."
        ]
        for item in unread[:max_items]:
            summary = item.summary
            if len(summary) > 160:
                summary = summary[:157] + "..."
            age = ""
            try:
                secs = max(0, int((now - item.created_at).total_seconds()))
                if secs < 120:
                    age = f", age={secs}s"
                elif secs < 3600:
                    age = f", age={secs // 60}m"
                elif secs < 86400:
                    age = f", age={secs // 3600}h"
                else:
                    age = f", age={secs // 86400}d (stale — only mention if asked)"
            except Exception:
                age = ""
            lines.append(
                f"[UNREAD] {item.title}: {summary} (id: {item.id}{age})"
            )
        remaining = len(unread) - max_items
        if remaining > 0:
            lines.append(f"...and {remaining} more unread result(s)")
        return "\n".join(lines)

    def clear(self):
        with self._lock:
            self._items.clear()
            self._persist()


def get_inbox() -> Inbox:
    return Inbox()
