from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


@dataclass
class ConversationThread:
    id: str
    created_at: str
    updated_at: str
    name: str = "New Conversation"
    summary: str = ""
    messages: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def create_new(cls) -> "ConversationThread":
        now = datetime.now().isoformat()
        conv_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        return cls(id=conv_id, created_at=now, updated_at=now)

    def add_message(self, role: str, content: str, tool_call_id: str | None = None) -> None:
        payload: dict[str, Any] = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
        }
        if tool_call_id:
            payload["tool_call_id"] = tool_call_id
        self.messages.append(payload)
        self.updated_at = datetime.now().isoformat()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "messages": self.messages,
            "summary": self.summary,
            "topics": [],
        }


class ConversationStore:
    """Simple JSON-backed conversation storage for server-side history."""

    def __init__(self, conversations_dir: Path):
        self.conversations_dir = conversations_dir
        self.index_path = conversations_dir / "index.json"
        self._lock = threading.Lock()
        self.current: ConversationThread | None = None
        self.index: dict[str, dict[str, Any]] = {}
        self._ensure_storage()
        self._load_index()

    def _ensure_storage(self) -> None:
        self.conversations_dir.mkdir(parents=True, exist_ok=True)

    def _load_index(self) -> None:
        if not self.index_path.exists():
            self.index = {}
            return
        try:
            self.index = json.loads(self.index_path.read_text(encoding="utf-8"))
        except Exception:
            self.index = {}

    def _save_index(self) -> None:
        self.index_path.write_text(json.dumps(self.index, indent=2, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def _coerce_title(conv: ConversationThread) -> str:
        user_messages = [m.get("content", "") for m in conv.messages if m.get("role") == "user"]
        if not user_messages:
            return f"Conversation {conv.id}"
        first = user_messages[0].strip().replace("\n", " ")
        return (first[:48] + "...") if len(first) > 51 else (first or f"Conversation {conv.id}")

    @staticmethod
    def _coerce_summary(conv: ConversationThread) -> str:
        combined = []
        for msg in conv.messages:
            role = msg.get("role", "")
            if role in ("user", "assistant"):
                snippet = str(msg.get("content", "")).strip().replace("\n", " ")
                if snippet:
                    combined.append(f"{role}: {snippet[:90]}")
            if len(combined) >= 2:
                break
        return " | ".join(combined)[:180]

    def _persist(self, conv: ConversationThread) -> None:
        if conv.name == "New Conversation":
            conv.name = self._coerce_title(conv)
        if not conv.summary:
            conv.summary = self._coerce_summary(conv)

        file_path = self.conversations_dir / f"{conv.id}.json"
        file_path.write_text(json.dumps(conv.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")

        self.index[conv.id] = {
            "name": conv.name,
            "created_at": conv.created_at,
            "updated_at": conv.updated_at,
            "summary": conv.summary,
            "message_count": len(conv.messages),
        }
        self._save_index()

    def start(self) -> str:
        with self._lock:
            if self.current and self.current.messages:
                self._persist(self.current)
            self.current = ConversationThread.create_new()
            return self.current.id

    def add_message(self, role: str, content: str, tool_call_id: str | None = None) -> str:
        with self._lock:
            if self.current is None:
                self.current = ConversationThread.create_new()
            self.current.add_message(role=role, content=content, tool_call_id=tool_call_id)
            return self.current.id

    def save_current(self) -> None:
        with self._lock:
            if not self.current:
                return
            self._persist(self.current)

    def list_recent(self, limit: int = 10) -> list[dict[str, Any]]:
        with self._lock:
            items = sorted(
                self.index.items(),
                key=lambda entry: entry[1].get("updated_at", ""),
                reverse=True,
            )
            output: list[dict[str, Any]] = []
            for conv_id, info in items[:limit]:
                output.append(
                    {
                        "id": conv_id,
                        "name": info.get("name", "Unnamed"),
                        "summary": info.get("summary", ""),
                        "date": str(info.get("created_at", ""))[:10],
                    }
                )
            return output

    def recent_hour_context(self, limit: int = 5) -> str:
        now = datetime.now()
        minimum = now - timedelta(hours=1)
        items: list[tuple[str, dict[str, Any], datetime]] = []

        with self._lock:
            for conv_id, info in self.index.items():
                updated = info.get("updated_at") or info.get("created_at")
                if not updated:
                    continue
                try:
                    updated_dt = datetime.fromisoformat(str(updated))
                except ValueError:
                    continue
                if updated_dt >= minimum:
                    items.append((conv_id, info, updated_dt))

        if not items:
            return ""

        items.sort(key=lambda row: row[2], reverse=True)

        lines = ["[RECENT CONVERSATIONS (past hour) - You can reference these if relevant]"]
        for _, info, updated_dt in items[:limit]:
            lines.append(
                f"- \"{info.get('name', 'Unnamed')}\" (at {updated_dt.strftime('%H:%M')}): {str(info.get('summary', 'No summary'))[:150]}"
            )
        return "\n".join(lines)
