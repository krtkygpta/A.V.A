from __future__ import annotations

import json
import threading
import time
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
    topics: list[str] = field(default_factory=list)

    @classmethod
    def create_new(cls) -> "ConversationThread":
        now = datetime.now().isoformat()
        import uuid
        conv_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:4]}"
        return cls(id=conv_id, created_at=now, updated_at=now)

    def add_message(
        self,
        role: str,
        content: str,
        tool_call_id: str | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
        tool_steps: list[dict[str, Any]] | None = None,
        name: str | None = None,
    ) -> None:
        if self.messages:
            last = self.messages[-1]
            if last.get("role") == role and str(last.get("content", "")).strip() == str(content).strip() and last.get("tool_call_id") == tool_call_id:
                return
        payload: dict[str, Any] = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
        }
        if tool_call_id:
            payload["tool_call_id"] = tool_call_id
        if name:
            payload["name"] = name
        if tool_calls:
            payload["tool_calls"] = tool_calls
        if tool_steps:
            payload["tool_steps"] = tool_steps
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
            "topics": self.topics,
        }


class ConversationStore:
    """Simple JSON-backed conversation storage for server-side history."""

    def __init__(self, conversations_dir: Path):
        self.conversations_dir = conversations_dir
        self.index_path = conversations_dir / "index.json"
        self._lock = threading.Lock()
        self.active_threads: dict[str, ConversationThread] = {}
        self.client_active_conv: dict[str, str] = {}
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

    def _load_thread_from_disk(self, conv_id: str) -> ConversationThread | None:
        file_path = self.conversations_dir / f"{conv_id}.json"
        if not file_path.exists():
            return None
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
            thread = ConversationThread(
                id=data.get("id", conv_id),
                created_at=data.get("created_at", ""),
                updated_at=data.get("updated_at", ""),
                name=data.get("name", "Conversation"),
                summary=data.get("summary", ""),
                messages=data.get("messages", []),
                topics=data.get("topics", []),
            )
            return thread
        except Exception:
            return None

    @staticmethod
    def _is_placeholder_name(name: str) -> bool:
        import re
        if not name or name.strip() in ("New Conversation", "Unnamed", "Conversation"):
            return True
        if re.match(r"^(?:Conversation|Chat)\b", name.strip(), re.IGNORECASE):
            return True
        return False

    @classmethod
    def _coerce_title(cls, conv: ConversationThread) -> str:
        import re
        user_messages = [m.get("content", "") for m in conv.messages if m.get("role") == "user"]
        if not user_messages:
            return "New Conversation"
        first = str(user_messages[0]).strip().replace("\n", " ")
        # Strip user name prefix (e.g. "Kartikey: ", "User: ")
        first = re.sub(r"^[A-Za-z0-9_\-\s]+:\s*", "", first).strip()
        if not first:
            return "New Conversation"

        # Heuristic title extraction: clean filler words and keep 3-6 words
        words = first.split()
        if words:
            # Strip initial conversational filler
            while words and words[0].lower().strip(".,!?:;") in (
                "can", "could", "please", "hey", "ava", "tell", "show", "what", "how", "you", "me", "is", "are", "the", "uh", "uhuh"
            ):
                words = words[1:]
            if words:
                clean_title = " ".join(words[:6]).strip(".,!?:;").title()
                if clean_title:
                    return clean_title[:45]

        return (first[:42] + "...") if len(first) > 45 else first

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
        user_msgs = [m for m in conv.messages if m.get("role") == "user"]
        if self._is_placeholder_name(conv.name) and user_msgs:
            conv.name = self._coerce_title(conv)
        elif not conv.name:
            conv.name = "New Conversation"
        if not conv.summary and user_msgs:
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

    def start(self, client_id: str = "default", name: str = "New Conversation") -> str:
        """Starts a fresh conversation thread for a given client."""
        with self._lock:
            thread = ConversationThread.create_new()
            thread.name = name
            self.active_threads[thread.id] = thread
            self.client_active_conv[client_id] = thread.id
            self.current = thread
            self._persist(thread)
            return thread.id

    def add_message(
        self,
        role: str,
        content: str,
        tool_call_id: str | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
        tool_steps: list[dict[str, Any]] | None = None,
        name: str | None = None,
        conv_id: str | None = None,
        client_id: str = "default",
    ) -> str:
        """Adds a message to an explicit conversation ID or the client's current conversation."""
        with self._lock:
            target_id = conv_id or self.client_active_conv.get(client_id)
            thread = self.active_threads.get(target_id) if target_id else None

            if not thread and target_id:
                thread = self._load_thread_from_disk(target_id)

            if not thread:
                thread = ConversationThread.create_new()
                self.client_active_conv[client_id] = thread.id

            thread.add_message(
                role=role,
                content=content,
                tool_call_id=tool_call_id,
                tool_calls=tool_calls,
                tool_steps=tool_steps,
                name=name,
            )
            self.active_threads[thread.id] = thread
            self.client_active_conv[client_id] = thread.id
            self.current = thread
            self._persist(thread)
            return thread.id

    def get_thread(self, conv_id: str) -> dict[str, Any] | None:
        with self._lock:
            thread = self.active_threads.get(conv_id)
            if not thread:
                thread = self._load_thread_from_disk(conv_id)
            return thread.to_dict() if thread else None

    def import_thread(self, conv_data: dict[str, Any]) -> str:
        """Import or update an existing conversation thread from another client."""
        with self._lock:
            conv_id = str(conv_data.get("id") or "").strip()
            if not conv_id:
                conv_id = datetime.now().strftime("%Y%m%d_%H%M%S")

            thread = ConversationThread(
                id=conv_id,
                name=conv_data.get("name") or "Conversation",
                created_at=conv_data.get("created_at") or datetime.now().isoformat(),
                updated_at=conv_data.get("updated_at") or datetime.now().isoformat(),
                messages=conv_data.get("messages") or [],
                summary=conv_data.get("summary") or "",
                topics=conv_data.get("topics") or [],
            )
            self.active_threads[conv_id] = thread
            self._persist(thread)
            return conv_id

    def save_current(self) -> None:
        with self._lock:
            for thread in list(self.active_threads.values()):
                self._persist(thread)

    def list_recent(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            items = sorted(
                self.index.items(),
                key=lambda entry: entry[1].get("updated_at", ""),
                reverse=True,
            )
            output: list[dict[str, Any]] = []
            for conv_id, info in items[:limit]:
                up_str = info.get("updated_at") or info.get("created_at") or ""
                ts_ms = 0
                if up_str:
                    try:
                        ts_ms = int(datetime.fromisoformat(str(up_str)).timestamp() * 1000)
                    except Exception:
                        pass
                output.append(
                    {
                        "id": conv_id,
                        "name": info.get("name", "Unnamed"),
                        "summary": info.get("summary", ""),
                        "date": str(info.get("created_at", ""))[:10],
                        "created_at": info.get("created_at", ""),
                        "updated_at": info.get("updated_at", ""),
                        "message_count": info.get("message_count", 0),
                        "timestamp": ts_ms or int(time.time() * 1000),
                    }
                )
            return output

    def delete_thread(self, conv_id: str) -> bool:
        """Deletes a conversation thread from storage and index."""
        with self._lock:
            removed = False
            if conv_id in self.index:
                self.index.pop(conv_id, None)
                removed = True
            if conv_id in self.active_threads:
                self.active_threads.pop(conv_id, None)
                removed = True
            for client_id, active_id in list(self.client_active_conv.items()):
                if active_id == conv_id:
                    self.client_active_conv.pop(client_id, None)
            if self.current and self.current.id == conv_id:
                self.current = None
            file_path = self.conversations_dir / f"{conv_id}.json"
            try:
                if file_path.exists():
                    file_path.unlink()
                    removed = True
            except OSError:
                pass
            if removed:
                self._save_index()
            return removed

    def reload_index(self) -> None:
        with self._lock:
            self._load_index()

    def get_active_ids(self) -> set[str]:
        with self._lock:
            active = set(self.client_active_conv.values())
            if self.current and self.current.id:
                active.add(self.current.id)
            return active

    def prune_and_replace(self, removed_ids: list[str], consolidated: ConversationThread) -> None:
        """Atomically remove replaced small threads and persist the consolidated thread."""
        with self._lock:
            for cid in removed_ids:
                self.index.pop(cid, None)
                self.active_threads.pop(cid, None)
                for client_id, active_id in list(self.client_active_conv.items()):
                    if active_id == cid:
                        self.client_active_conv.pop(client_id, None)
                file_path = self.conversations_dir / f"{cid}.json"
                try:
                    if file_path.exists():
                        file_path.unlink()
                except OSError:
                    pass

            self.active_threads[consolidated.id] = consolidated
            self._persist(consolidated)

    def get_recent_chats_context(
        self,
        current_conv_id: str = "",
        min_chats: int = 5,
        updated_within_minutes: int = 15,
        max_total_chats: int = 8,
    ) -> str:
        """
        Builds a rich conversational context string containing transcripts of recent chats.
        Guarantees:
        - At minimum the last 5 conversations (excluding the current active one).
        - OR all conversations updated within the last 15 minutes (whichever set is larger, capped at max_total_chats).
        - Includes actual user and assistant dialogue exchanges so AVA has accurate memory.
        """
        now = datetime.now()
        cutoff_dt = now - timedelta(minutes=updated_within_minutes)
        all_items: list[tuple[str, dict[str, Any], datetime]] = []

        with self._lock:
            for conv_id, info in self.index.items():
                if current_conv_id and conv_id == current_conv_id:
                    continue
                updated = info.get("updated_at") or info.get("created_at")
                if not updated:
                    continue
                try:
                    updated_dt = datetime.fromisoformat(str(updated))
                except ValueError:
                    continue
                all_items.append((conv_id, info, updated_dt))

        if not all_items:
            return ""

        # Sort newest updated first
        all_items.sort(key=lambda row: row[2], reverse=True)

        # Collect all updated within last 15 minutes, and ensure at least min_chats
        selected: list[tuple[str, dict[str, Any], datetime]] = []
        for row in all_items:
            _, _, updated_dt = row
            if updated_dt >= cutoff_dt or len(selected) < min_chats:
                selected.append(row)
            if len(selected) >= max_total_chats:
                break

        if not selected:
            return ""

        sections = [
            "[CROSS-CONVERSATION MEMORY & RECENT CHATS TRANSCRIPTS]",
            "The user can start new conversations or switch between threads at any time. When the user asks "
            "'what did I talk to you about in the last chat?', 'what were we discussing?', or refers to past topics, "
            "rely on the actual transcripts below. Answer directly with specific details, specs, and outcomes without saying you don't know:\n"
        ]

        for idx, (conv_id, info, updated_dt) in enumerate(selected, 1):
            thread_data = self.get_thread(conv_id)
            if not thread_data:
                continue
            msgs = thread_data.get("messages", [])
            if not msgs:
                continue

            diff_secs = max(0, (now - updated_dt).total_seconds())
            if diff_secs < 60:
                time_str = "just now"
            elif diff_secs < 3600:
                time_str = f"{int(diff_secs // 60)}m ago"
            elif diff_secs < 86400:
                time_str = f"{int(diff_secs // 3600)}h ago"
            else:
                time_str = f"{int(diff_secs // 86400)}d ago"

            header_label = f"Previous Chat #{idx}"
            if idx == 1:
                header_label += " (MOST RECENT / LAST CHAT)"

            conv_name = info.get("name") or thread_data.get("name") or "Conversation"
            sections.append(
                f"=== {header_label}: \"{conv_name}\" (ID: {conv_id} | Updated: {time_str} / {updated_dt.strftime('%H:%M:%S')}) ==="
            )

            # Include up to the last 8 messages of this conversation
            recent_msgs = msgs[-8:] if len(msgs) > 8 else msgs
            for m in recent_msgs:
                role = (m.get("role") or "").upper()
                if role not in ("USER", "ASSISTANT"):
                    continue
                speaker = "User" if role == "USER" else "AVA"
                txt = str(m.get("content") or "").strip()
                if len(txt) > 1200:
                    txt = txt[:1200] + "..."
                if txt:
                    sections.append(f"{speaker}: {txt}")
            sections.append("")

        return "\n".join(sections).strip()

    def recent_hour_context(self, limit: int = 5, current_conv_id: str = "") -> str:
        """Alias for backward compatibility, returns rich recent chats context."""
        return self.get_recent_chats_context(current_conv_id=current_conv_id, min_chats=limit, updated_within_minutes=15)

    def inspect_or_search(
        self,
        action: str = "get",
        conv_id: str | None = None,
        query: str | None = None,
        limit: int = 20,
        current_conv_id: str = "",
    ) -> dict[str, Any]:
        """
        Retrieve, inspect, or search conversation history for tool calling.
        Actions:
        - 'get': Retrieve messages from a specific conversation ID, or 'last' / 'previous' / 'latest'.
        - 'list': List recent conversations with their IDs, names, timestamps, and message counts.
        - 'search': Search conversations matching a query string.
        """
        action = (action or "get").lower().strip()
        limit = max(1, min(limit or 20, 50))

        if action in ("get", "read", "view", "fetch"):
            target_id = (conv_id or "").strip()
            if not target_id or target_id.lower() in ("last", "previous", "prior", "latest", "recent"):
                with self._lock:
                    sorted_items = sorted(
                        self.index.items(),
                        key=lambda entry: entry[1].get("updated_at", ""),
                        reverse=True,
                    )
                    for cid, _ in sorted_items:
                        if current_conv_id and cid == current_conv_id:
                            continue
                        target_id = cid
                        break

            if not target_id:
                return {"status": "error", "message": "No previous conversation found."}

            thread_data = self.get_thread(target_id)
            if not thread_data:
                return {"status": "error", "message": f"Conversation '{target_id}' not found."}

            msgs = thread_data.get("messages", [])
            sliced_msgs = msgs[-limit:] if len(msgs) > limit else msgs
            return {
                "status": "success",
                "conversation_id": thread_data.get("id"),
                "name": thread_data.get("name"),
                "created_at": thread_data.get("created_at"),
                "updated_at": thread_data.get("updated_at"),
                "total_messages": len(msgs),
                "messages": [
                    {
                        "role": m.get("role"),
                        "content": m.get("content"),
                        "timestamp": m.get("timestamp"),
                        "tool_call_id": m.get("tool_call_id"),
                        "tool_calls": m.get("tool_calls"),
                        "tool_steps": m.get("tool_steps"),
                        "name": m.get("name"),
                    }
                    for m in sliced_msgs
                ],
            }

        elif action in ("list", "all", "recent"):
            with self._lock:
                sorted_items = sorted(
                    self.index.items(),
                    key=lambda entry: entry[1].get("updated_at", ""),
                    reverse=True,
                )
            results = []
            for cid, info in sorted_items[:limit]:
                is_curr = (cid == current_conv_id)
                results.append({
                    "conversation_id": cid,
                    "name": info.get("name", "Conversation"),
                    "updated_at": info.get("updated_at", ""),
                    "message_count": info.get("message_count", 0),
                    "summary": info.get("summary", ""),
                    "is_current": is_curr,
                })
            return {"status": "success", "count": len(results), "conversations": results}

        elif action in ("search", "find", "query"):
            q = (query or "").lower().strip()
            if not q:
                return {"status": "error", "message": "Missing search query."}

            matches = []
            with self._lock:
                sorted_ids = [
                    cid for cid, _ in sorted(
                        self.index.items(),
                        key=lambda entry: entry[1].get("updated_at", ""),
                        reverse=True,
                    )
                ]

            for cid in sorted_ids:
                thread_data = self.get_thread(cid)
                if not thread_data:
                    continue
                conv_name = thread_data.get("name", "")
                matching_msgs = []
                for m in thread_data.get("messages", []):
                    content = str(m.get("content") or "")
                    if q in content.lower() or q in conv_name.lower():
                        matching_msgs.append({
                            "role": m.get("role"),
                            "snippet": content[:300],
                            "timestamp": m.get("timestamp"),
                        })
                if matching_msgs or q in conv_name.lower():
                    matches.append({
                        "conversation_id": cid,
                        "name": conv_name,
                        "updated_at": thread_data.get("updated_at"),
                        "matching_messages": matching_msgs[-4:],
                    })
                if len(matches) >= limit:
                    break

            return {
                "status": "success",
                "query": query,
                "matches_count": len(matches),
                "results": matches,
            }

        return {
            "status": "error",
            "message": f"Unsupported action: '{action}'. Supported actions: 'get' (retrieve messages), 'list' (recent conversations), 'search' (find by query).",
        }
