"""
delegate_session.py - Persistent session management for AVA's Delegated AI Agent.

Enables multi-turn conversational memory and session continuation for background
delegate workers. The main agent can start a new delegate session or continue
an existing one via session_id or continue_session=True.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
import threading
from typing import Any, Dict, List, Optional, Tuple
import uuid

# Storage path: App/data/tasks/delegate_sessions.json
DELEGATE_SESSIONS_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "data"
    / "tasks"
    / "delegate_sessions.json"
)

_lock = threading.RLock()


@dataclass
class DelegateSessionRecord:
    session_id: str
    created_at: str = ""
    updated_at: str = ""
    title: str = ""
    specialist: str = "generic"
    status: str = "active"
    task_id: str = ""
    messages: List[Dict[str, Any]] = field(default_factory=list)
    summary: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "title": self.title,
            "specialist": self.specialist,
            "status": self.status,
            "task_id": self.task_id,
            "messages": self.messages,
            "summary": self.summary,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> DelegateSessionRecord:
        return cls(
            session_id=str(data.get("session_id") or "").strip(),
            created_at=str(data.get("created_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
            title=str(data.get("title") or ""),
            specialist=str(data.get("specialist") or "generic"),
            status=str(data.get("status") or "active"),
            task_id=str(data.get("task_id") or ""),
            messages=list(data.get("messages") or []),
            summary=str(data.get("summary") or ""),
            metadata=dict(data.get("metadata") or {}),
        )


class DelegateSessionStore:
    """Thread-safe JSON file store for delegate agent sessions."""

    def __init__(self, path: Optional[Path] = None):
        self.path = path or DELEGATE_SESSIONS_PATH
        self._sessions: Dict[str, DelegateSessionRecord] = {}
        self._load()

    def _load(self) -> None:
        with _lock:
            if not self.path.exists():
                return
            try:
                raw = self.path.read_text(encoding="utf-8")
                if not raw.strip():
                    return
                data = json.loads(raw)
                sessions_list = data.get("sessions") or []
                for item in sessions_list:
                    rec = DelegateSessionRecord.from_dict(item)
                    if rec.session_id:
                        self._sessions[rec.session_id] = rec
            except Exception as e:
                print(f"[DelegateSessionStore] load failed: {e}")

    def _save(self) -> None:
        with _lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                payload = {
                    "updated_at": datetime.now().isoformat(),
                    "sessions": [s.to_dict() for s in self._sessions.values()],
                }
                tmp_path = self.path.with_suffix(".tmp")
                tmp_path.write_text(
                    json.dumps(payload, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                tmp_path.replace(self.path)
            except Exception as e:
                print(f"[DelegateSessionStore] save failed: {e}")

    def upsert(self, rec: DelegateSessionRecord) -> None:
        with _lock:
            now = datetime.now().isoformat()
            rec.updated_at = now
            existing = self._sessions.get(rec.session_id)
            if existing and not rec.created_at:
                rec.created_at = existing.created_at
            elif not rec.created_at:
                rec.created_at = now
            self._sessions[rec.session_id] = rec
            self._save()

    def get(self, session_id: str) -> Optional[DelegateSessionRecord]:
        with _lock:
            return self._sessions.get(session_id)

    def get_last_session(self) -> Optional[DelegateSessionRecord]:
        with _lock:
            items = list(self._sessions.values())
        if not items:
            return None
        items.sort(key=lambda s: s.updated_at or s.created_at, reverse=True)
        return items[0]

    def list_recent(self, limit: int = 20) -> List[DelegateSessionRecord]:
        with _lock:
            items = list(self._sessions.values())
        items.sort(key=lambda s: s.updated_at or s.created_at, reverse=True)
        return items[:limit]

    def find_by_description(self, query: str, limit: int = 5) -> List[DelegateSessionRecord]:
        q = (query or "").lower().strip()
        if not q:
            return self.list_recent(limit)
        scored: List[Tuple[int, DelegateSessionRecord]] = []
        for s in self.list_recent(50):
            blob = f"{s.session_id} {s.title} {s.specialist} {s.summary}".lower()
            score = sum(1 for tok in q.split() if tok in blob)
            if score or q in blob:
                scored.append((score, s))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [s for _, s in scored[:limit]]

    def delete(self, session_id: str) -> bool:
        with _lock:
            if session_id in self._sessions:
                del self._sessions[session_id]
                self._save()
                return True
            return False


_delegate_store: Optional[DelegateSessionStore] = None


def get_delegate_session_store() -> DelegateSessionStore:
    global _delegate_store
    if _delegate_store is None:
        _delegate_store = DelegateSessionStore()
    return _delegate_store


def generate_delegate_session_id() -> str:
    """Generate a clean unique session ID for a delegate worker."""
    return f"del_{uuid.uuid4().hex[:8]}"
