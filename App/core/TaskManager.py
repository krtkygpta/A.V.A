"""
TaskManager.py - Manages background tasks and sub-agents for AVA

This module provides:
1. TaskRegistry - Track all running background tasks
2. CompletionQueue - Store completed task results for notification
3. SubAgent - Base class for background workers
4. Notification system that respects main agent state
5. Conversation-aware task tracking for proper notification routing
6. Decision engine for intelligent notification delivery

ENHANCED: Now includes conversation association, state management, and
intelligent notification delivery for all three use cases:
- Case 1: Original conversation still active
- Case 2: No conversation active (wake-word mode)
- Case 3: Different conversation active
"""

import json
import os
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from collections import defaultdict


TASK_STORE_PATH = Path(__file__).resolve().parent.parent / "data" / "tasks" / "tasks.json"

# Job kind controls *inbox stickiness*, not whether work survives chats.
# All background jobs keep running across conversation changes / sleep.
#
#   long  → when done: desktop notify + sticky unread inbox until result/dismiss
#   short → when done: one-shot alert (desktop ± speak once); NEVER re-nag on
#           every new conversation. Timers are short: they hold over chats
#           while counting down, then fire once and go quiet.
KIND_BY_TASK_TYPE = {
    "research": "long",
    "mission": "long",
    "delegate": "long",
    "opencode": "long",
    "coding": "long",
    "demo": "long",  # timed test job — sticky inbox by default
    "timer": "short",
    "scrape": "short",
    "smarthome": "short",
}
DEFAULT_KIND = "short"


def infer_kind(task_type: str, kind: Optional[str] = None) -> str:
    """Resolve a job's kind, honouring an explicit override first."""
    if kind in ("short", "long"):
        return kind
    return KIND_BY_TASK_TYPE.get(task_type or "general", DEFAULT_KIND)


def is_sticky_inbox(kind: str) -> bool:
    return kind == "long"


# How loudly to alert when a job finishes. Chosen by the main LLM at spawn time.
# Default is SILENT — no beep/toast; main agent decides after DONE (ping / send_notification).
# Real alarms only: short | continuous set at spawn.
ALERT_MODES = ("silent", "notify", "short", "continuous")
DEFAULT_ALERT_MODE = "silent"


def normalize_alert_mode(mode: Optional[str]) -> str:
    """Map free-form / tool args to silent | notify | short | continuous."""
    if mode is None:
        return DEFAULT_ALERT_MODE
    m = str(mode).strip().lower()
    if m in ("continuous", "persist", "persistent", "ring", "alarm", "until_response"):
        return "continuous"
    if m in ("short", "ping", "chime", "beep"):
        return "short"
    if m in ("notify", "notification", "toast", "desktop"):
        return "notify"
    if m in ("silent", "none", "off", "quiet", "mute", "no_alert", "no-alert"):
        return "silent"
    if m in ALERT_MODES:
        return m
    return DEFAULT_ALERT_MODE


def alert_mode_wants_sound(mode: Optional[str]) -> bool:
    return normalize_alert_mode(mode) in ("short", "continuous")


def alert_mode_wants_toast(mode: Optional[str]) -> bool:
    return normalize_alert_mode(mode) in ("notify", "short", "continuous")


# Max nested follow-up depth (timer → research → …). Prevents infinite chains.
try:
    MAX_FOLLOW_UP_DEPTH = max(1, int(os.getenv("AVA_MAX_FOLLOW_UP_DEPTH", "5")))
except (TypeError, ValueError):
    MAX_FOLLOW_UP_DEPTH = 5

# Allowed keys when spawning a follow-up (passed into dispatch_background_task).
_FOLLOW_UP_ARG_KEYS = (
    "task_type",
    "type",
    "topic",
    "level",
    "research_level",
    "url",
    "purpose",
    "duration",
    "message",
    "alert_mode",
    "command",
    "objective",
    "mission",
    "context",
    "expected_output",
    "max_workers",
    "ask",
    "question",
    "require_user",
    "default_if_timeout",
    "kind",
    "difficulty",
    "model_tier",
    "follow_up",  # nested chain
    # main_action (lightweight — no background worker)
    "action",
    "instruction",
    # opencode
    "prompt",
    "directory",
    "dir",
    "session_id",
    "session",
    "title",
    "description",
    "permissions",
    "permission_mode",
    "continue_session",
    "model",  # optional; omit to use OpenCode default
)

# Follow-ups that run on the MAIN agent (full tools), not a background worker.
MAIN_ACTION_TYPES = frozenset(
    {
        "main",
        "main_action",
        "action",
        "do",
        "foreground",
    }
)


def parse_follow_up_specs(raw: Any) -> List[Dict[str, Any]]:
    """
    Normalize follow_up tool arg into a list of task-spec dicts.

    Accepts:
      - dict: one next task
      - list[dict]: sequential chain (first runs; rest nested as its follow_up)
      - JSON string of either
    """
    if raw is None or raw == "" or raw is False:
        return []
    data = raw
    if isinstance(raw, str):
        s = raw.strip()
        if not s or s.lower() in ("null", "none", "{}"):
            return []
        try:
            data = json.loads(s)
        except json.JSONDecodeError:
            print(f"[TaskManager] follow_up is not valid JSON: {s[:120]!r}")
            return []
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict) and x]
    return []


def _sanitize_follow_up_kwargs(spec: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in (spec or {}).items():
        if k in _FOLLOW_UP_ARG_KEYS and v is not None:
            out[k] = v
    return out


def spawn_follow_up_tasks(parent: "BackgroundTask") -> List[str]:
    """
    When a job completes successfully, auto-dispatch any follow_up tasks.

    Binds children to the *current* conversation if one is active (so chains
    work after /new), otherwise keeps the parent's conversation ids.
    Returns list of new task ids (empty if none).
    """
    if parent is None or parent.status != TaskStatus.COMPLETED:
        return []

    meta = parent.metadata or {}
    # Already spawned (idempotent if completion routing retries)
    if meta.get("follow_up_spawned_ids"):
        return list(meta.get("follow_up_spawned_ids") or [])

    specs = parse_follow_up_specs(meta.get("follow_up"))
    if not specs:
        return []

    depth = int(meta.get("follow_up_depth") or 0)
    if depth >= MAX_FOLLOW_UP_DEPTH:
        print(
            f"[TaskManager] follow_up depth {depth} >= max {MAX_FOLLOW_UP_DEPTH}; "
            f"not spawning from {parent.id}"
        )
        return []

    state = ConversationStateManager()
    conv_id = (state.current_conversation_id or "").strip() or parent.conversation_id
    conv_name = (
        (state.current_conversation_name or "").strip() or parent.conversation_name
    )

    # Sequential list → nest rest under the first child's follow_up
    head = _sanitize_follow_up_kwargs(dict(specs[0]))
    if len(specs) > 1:
        head["follow_up"] = specs[1:]

    task_type = (
        head.pop("task_type", None) or head.pop("type", None) or ""
    ).strip().lower()
    if not task_type:
        print(f"[TaskManager] follow_up missing task_type on parent {parent.id}")
        return []

    registry = TaskRegistry()

    # ── Lightweight path: main agent does the next step (full tools) ─────
    # Use for small post-timer actions (WhatsApp, notify, one tool call).
    # Do NOT spawn a delegate LLM worker for these.
    if task_type in MAIN_ACTION_TYPES:
        action_text = (
            head.get("objective")
            or head.get("action")
            or head.get("instruction")
            or head.get("message")
            or head.get("topic")
            or ""
        )
        action_text = str(action_text).strip()
        if not action_text:
            print(f"[TaskManager] main_action follow_up missing objective on {parent.id}")
            with registry._tasks_lock:
                live = registry._tasks.get(parent.id)
                if live is not None:
                    live.metadata["follow_up_error"] = "main_action missing objective"
                    registry._persist_locked()
            return []

        marker = f"main:{parent.id}"
        with registry._tasks_lock:
            live = registry._tasks.get(parent.id)
            if live is not None:
                live.metadata["follow_up_spawned_ids"] = [marker]
                live.metadata["follow_up_main_action"] = action_text
                live.metadata["follow_up_spawn_note"] = "main_action queued for main agent"
                live.metadata["announce_policy"] = "result_only"
                note = f' | Next: main agent should do: "{action_text[:120]}"'
                if live.result is None:
                    live.result = note.strip(" |")
                else:
                    live.result = f"{live.result}{note}"
                # Remaining chain (if list had more steps) → attach as nested
                # follow_up on a synthetic note only; main completes first action.
                rest = head.get("follow_up")
                if rest:
                    live.metadata["follow_up_after_main"] = rest
                registry._persist_locked()
        print(
            f"[TaskManager] follow_up main_action queued from {parent.id}: "
            f"{action_text[:100]!r}"
        )
        return [marker]

    # ── Heavy path: real background job (research, timer, delegate, …) ───
    head["_follow_up_depth"] = depth + 1
    head["_follow_up_parent_id"] = parent.id

    try:
        child_id, msg = dispatch_background_task(
            task_type,
            conversation_id=conv_id,
            conversation_name=conv_name,
            **head,
        )
    except Exception as e:
        print(f"[TaskManager] follow_up dispatch failed from {parent.id}: {e}")
        with registry._tasks_lock:
            live = registry._tasks.get(parent.id)
            if live is not None:
                live.metadata["follow_up_error"] = str(e)
                registry._persist_locked()
        return []

    spawned: List[str] = []
    if child_id:
        spawned.append(child_id)
        print(
            f"[TaskManager] follow_up spawned {child_id} ({task_type}) "
            f"from parent {parent.id} → conv={conv_id or '(none)'}"
        )
    else:
        print(f"[TaskManager] follow_up failed to start: {msg}")

    with registry._tasks_lock:
        live = registry._tasks.get(parent.id)
        if live is not None:
            live.metadata["follow_up_spawned_ids"] = spawned
            live.metadata["follow_up_spawn_note"] = msg
            if spawned:
                note = (
                    f" | Auto-started follow-up: {task_type} id={spawned[0]}"
                )
                if live.result is None:
                    live.result = note.strip(" |")
                else:
                    live.result = f"{live.result}{note}"
            registry._persist_locked()
    return spawned


# ============================================================================
# ENUMS
# ============================================================================

class TaskStatus(Enum):
    """Status of a background task"""
    PENDING = "pending"
    RUNNING = "running"
    NEEDS_INPUT = "needs_input"  # blocked on ClarificationBridge
    COMPLETED = "completed"
    FAILED = "failed"
    NOTIFIED = "notified"
    CANCELLED = "cancelled"


class TaskPriority(Enum):
    """Priority levels for background tasks"""
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class DeliveryMethod(Enum):
    """How to deliver a task completion notification"""
    IMMEDIATE = "immediate"
    NEXT_RESPONSE = "next_response"
    QUEUE_FOR_IDLE = "queue_for_idle"
    DEFER = "defer"


class NotificationPreference(Enum):
    """User preference for how to be notified"""
    IMMEDIATE = "immediate"
    NEXT_TURN = "next_turn"
    IDLE_ONLY = "idle_only"
    NEVER = "never"


# ============================================================================
# DATA CLASSES
# ============================================================================

@dataclass
class BackgroundTask:
    """
    Enhanced task with conversation association and metadata.
    """
    id: str
    name: str
    description: str
    status: TaskStatus = TaskStatus.PENDING
    result: Any = None
    error: Optional[str] = None
    conversation_id: str = ""
    conversation_name: str = ""
    original_query: str = ""
    task_type: str = "general"
    kind: str = "short"  # short=one-shot alert; long=sticky inbox (both outlive chats)
    priority: TaskPriority = TaskPriority.NORMAL
    notification_preference: NotificationPreference = NotificationPreference.NEXT_TURN
    created_at: datetime = field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None
    notified_at: Optional[datetime] = None
    pending_request_id: Optional[str] = None  # active clarification, if any
    model_tier: str = "small"  # none|nano|small|main|heavy
    difficulty: str = "normal"  # easy|normal|hard
    # notify = desktop toast only; short = brief ping; continuous = ring until respond
    alert_mode: str = DEFAULT_ALERT_MODE
    artifact_path: Optional[str] = None  # full result on disk for long jobs
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_summary(self) -> str:
        if self.status == TaskStatus.COMPLETED:
            result_str = str(self.result)[:500] if self.result else "No result"
            return f"[BACKGROUND TASK COMPLETED] Task '{self.name}' (from conversation '{self.conversation_name}'): {result_str}"
        elif self.status == TaskStatus.FAILED:
            return f"[BACKGROUND TASK FAILED] Task '{self.name}': {self.error}"
        elif self.status == TaskStatus.NOTIFIED:
            return f"[TASK NOTIFIED] Task '{self.name}' results were delivered"
        return f"[TASK IN PROGRESS] Task '{self.name}' (started in conversation '{self.conversation_name}')"

    def _result_brief(self) -> dict:
        try:
            from core.jobs.context import parse_result_brief

            return parse_result_brief(self.result if self.result is not None else self.error)
        except Exception:
            raw = str(self.result or self.error or "").strip()
            if len(raw) > 220:
                raw = raw[:217] + "..."
            return {"summary": raw, "report_path": "", "level": None}

    def to_ui_done(self) -> str:
        """
        Short system-line for the TUI chat log — never dump raw JSON or long
        agent instruction text into the visible chat.
        """
        brief = self._result_brief()
        status = self.status.value
        title = self.name or self.task_type or "task"
        if self.status == TaskStatus.FAILED:
            err = brief.get("summary") or self.error or "failed"
            return f"task failed · {title} · {err}"
        if self.status == TaskStatus.CANCELLED:
            return f"task cancelled · {title}"

        summary = (brief.get("summary") or "done").strip()
        if len(summary) > 140:
            summary = summary[:137].rsplit(" ", 1)[0] + "..."
        path = (brief.get("report_path") or brief.get("directory") or "").strip()
        if self.task_type in ("opencode", "coding"):
            sid = (brief.get("session_id") or "")[:24]
            line = f"done · opencode · {summary}"
            if path:
                line += f"\n{path}"
            if sid:
                line += f" · ses={sid}…" if len(brief.get("session_id") or "") > 24 else f" · {sid}"
            return line
        parts = [f"done · {title}", summary]
        fu_ids = (self.metadata or {}).get("follow_up_spawned_ids") or []
        if path:
            parts.append(f"saved: {path}")
        if fu_ids:
            parts.append(f"next: {', '.join(str(x) for x in fu_ids)}")
        if path:
            return f"{parts[0]} · {parts[1]}\n{parts[2]}" + (
                f"\n{parts[3]}" if len(parts) > 3 else ""
            )
        return " · ".join(parts)

    def to_compact_done(self) -> str:
        """
        Token-cheap DONE marker for the main agent.

        Dense facts only — no policy essays. Weak models greet when this is huge.
        """
        brief = self._result_brief()
        meta = self.metadata or {}
        summary = (brief.get("summary") or "").strip() or "completed"
        # OpenCode / workers: already cleaned; hard-cap for inject
        if len(summary) > 200:
            summary = summary[:197].rsplit(" ", 1)[0] + "..."

        # ── OpenCode: ultra-compact dedicated shape ──
        if self.task_type in ("opencode", "coding"):
            sid = (
                brief.get("session_id")
                or meta.get("opencode_session_id")
                or meta.get("session_id")
                or ""
            )
            directory = (
                brief.get("directory")
                or meta.get("opencode_directory")
                or meta.get("cwd")
                or brief.get("report_path")
                or ""
            )
            tools = brief.get("tools") or meta.get("tools") or []
            tool_s = ",".join(
                str(t).split(":")[0].strip() for t in tools[:5] if t
            )
            lines = [
                f"type=opencode id={self.id} status={brief.get('status') or self.status.value}",
                f"Summary: {summary}",
            ]
            if directory:
                lines.append(f"dir={directory}")
            if sid:
                lines.append(f"session_id={sid}")
            if tool_s:
                lines.append(f"tools={tool_s}")
            lines.append(
                "SPEAK: one short outcome for the user (what changed + how to run). "
                "Do NOT greet. Do NOT say hey/hello. Full detail: manage_background_tasks result."
            )
            return "\n".join(lines)

        # ── Default compact shape ──
        path = (brief.get("report_path") or "").strip()
        level = brief.get("level")
        lines = [
            f"Finished: {self.name or self.task_type} (id={self.id}, type={self.task_type})",
            f"Summary: {summary}",
        ]
        if path:
            lines.append(f"path: {path}")
        if level is not None:
            lines.append(f"level: {level}")
        if self.task_type == "timer":
            lines.append("Timer one-shot — do not re-mention later.")

        main_action = (meta.get("follow_up_main_action") or "").strip()
        fu_ids = meta.get("follow_up_spawned_ids") or []
        fu_err = meta.get("follow_up_error")

        if main_action:
            lines.append(f"REQUIRED ACTION: {main_action}")
            lines.append(
                "Call tools now, then ONE outcome line. No timer theater."
            )
        elif fu_ids:
            lines.append(
                f"Follow-up auto-started ids={','.join(str(x) for x in fu_ids)}. "
                "Do not re-spawn."
            )
        elif meta.get("follow_up") and not fu_ids and fu_err:
            lines.append(f"Follow-up failed: {fu_err}")

        # Tiny continue hint only (no full dossier dump — that bloated injects)
        sid = meta.get("opencode_session_id") or meta.get("session_id")
        if sid:
            lines.append(f"session_id={sid}")
        art = getattr(self, "artifact_path", None) or path
        if art and self.task_type == "research":
            lines.append(f"report: {art}")
        lines.append(
            "SPEAK: brief outcome only. No greetings. No process monologue."
        )
        return "\n".join(lines)

    def to_conversation_context(self) -> str:
        return f"""[BACKGROUND TASK CONTEXT]
Task ID: {self.id}
Type: {self.task_type}
Started by: Conversation "{self.conversation_name}" ({self.conversation_id})
Original Request: {self.original_query}
Status: {self.status.value}
Duration: {self.get_duration()}"""

    def to_notification_summary(self) -> str:
        if self.status == TaskStatus.COMPLETED:
            result_preview = str(self.result)[:200] if self.result else "completed successfully"
            conv_name = self.conversation_name or f"conversation {self.conversation_id}"
            return f"Task '{self.name}' (started in {conv_name}) has completed: {result_preview}"
        elif self.status == TaskStatus.FAILED:
            return f"Task '{self.name}' failed: {self.error}"
        elif self.status == TaskStatus.CANCELLED:
            return f"Task '{self.name}' was cancelled"
        return f"Task '{self.name}' status: {self.status.value}"

    def to_idle_notification(self) -> str:
        if self.status == TaskStatus.COMPLETED:
            conv_name = self.conversation_name or "a previous conversation"
            return f"Your background task '{self.name}' (from {conv_name}) has finished. Say my name to hear the results."
        elif self.status == TaskStatus.FAILED:
            return f"Your background task '{self.name}' failed. Say my name to see details."
        return f"Background task '{self.name}' has an update. Say my name to check."

    def to_different_conversation_notification(self) -> str:
        conv_name = self.conversation_name or "our previous conversation"
        if self.status == TaskStatus.COMPLETED:
            return f"Note: Your earlier task '{self.name}' (from {conv_name}) has finished."
        elif self.status == TaskStatus.FAILED:
            return f"Note: Your earlier task '{self.name}' failed: {self.error}"
        return f"Note: Your earlier task '{self.name}' has an update."

    def get_duration(self) -> str:
        if self.created_at and self.completed_at:
            delta = self.completed_at - self.created_at
            return str(delta)
        elif self.created_at:
            delta = datetime.now() - self.created_at
            return str(delta)
        return "unknown"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "status": self.status.value,
            "result": str(self.result) if self.result else None,
            "error": self.error,
            "conversation_id": self.conversation_id,
            "conversation_name": self.conversation_name,
            "original_query": self.original_query,
            "task_type": self.task_type,
            "kind": self.kind,
            "priority": self.priority.value,
            "created_at": self.created_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "notified_at": self.notified_at.isoformat() if self.notified_at else None,
            "notification_preference": self.notification_preference.value,
            "pending_request_id": self.pending_request_id,
            "model_tier": self.model_tier,
            "difficulty": self.difficulty,
            "alert_mode": normalize_alert_mode(self.alert_mode),
            "artifact_path": self.artifact_path,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BackgroundTask":
        raw_status = data.get("status", "pending")
        try:
            status = TaskStatus(raw_status)
        except ValueError:
            status = TaskStatus.PENDING
        task_type = data.get("task_type", "general")
        try:
            from core.jobs.model_router import infer_model_tier

            default_tier = infer_model_tier(task_type, data.get("difficulty"))
        except Exception:
            default_tier = "small"
        return cls(
            id=data.get("id", ""),
            name=data.get("name", "Unknown"),
            description=data.get("description", ""),
            status=status,
            result=data.get("result"),
            error=data.get("error"),
            conversation_id=data.get("conversation_id", ""),
            conversation_name=data.get("conversation_name", ""),
            original_query=data.get("original_query", ""),
            task_type=task_type,
            kind=infer_kind(task_type, data.get("kind")),
            priority=TaskPriority(data.get("priority", "normal")),
            created_at=datetime.fromisoformat(data.get("created_at", datetime.now().isoformat())),
            completed_at=datetime.fromisoformat(data.get("completed_at")) if data.get("completed_at") else None,
            notified_at=datetime.fromisoformat(data.get("notified_at")) if data.get("notified_at") else None,
            notification_preference=NotificationPreference(data.get("notification_preference", "next_turn")),
            pending_request_id=data.get("pending_request_id"),
            model_tier=data.get("model_tier") or default_tier,
            difficulty=data.get("difficulty") or "normal",
            alert_mode=normalize_alert_mode(data.get("alert_mode")),
            artifact_path=data.get("artifact_path"),
            metadata=data.get("metadata", {}),
        )


@dataclass
class NotificationStrategy:
    delivery_method: DeliveryMethod
    notification_text: str
    interrupt_current: bool = False
    priority: TaskPriority = TaskPriority.NORMAL
    task_id: str = ""


# ============================================================================
# TASK REGISTRY
# ============================================================================

class TaskRegistry:
    _instance = None
    _lock = threading.Lock()

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
        self._tasks: Dict[str, BackgroundTask] = {}
        self._conversation_tasks: Dict[str, List[str]] = defaultdict(list)
        self._tasks_lock = threading.RLock()
        self._load_persisted_tasks()
        self._initialized = True
        # After disk load: RUNNING/NEEDS_INPUT with no live thread → FAILED.
        try:
            from core.jobs.executor import get_executor

            get_executor().reclaim_orphans(self)
        except Exception as e:
            print(f"[TaskRegistry] Orphan reclaim skipped: {e}")

    def _load_persisted_tasks(self):
        try:
            if not TASK_STORE_PATH.exists():
                return
            data = json.loads(TASK_STORE_PATH.read_text(encoding="utf-8"))
            for item in data.get("tasks", []):
                task = BackgroundTask.from_dict(item)
                self._tasks[task.id] = task
                if task.conversation_id:
                    self._conversation_tasks[task.conversation_id].append(task.id)
        except Exception as e:
            print(f"[TaskRegistry] Could not load task store: {e}")

    def _persist_locked(self):
        try:
            TASK_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "updated_at": datetime.now().isoformat(),
                "tasks": [task.to_dict() for task in self._tasks.values()],
            }
            TASK_STORE_PATH.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as e:
            print(f"[TaskRegistry] Could not save task store: {e}")

    def create_task(self, name: str, description: str,
                   conversation_id: str = "", conversation_name: str = "",
                   original_query: str = "", task_type: str = "general",
                   kind: Optional[str] = None,
                   priority: TaskPriority = TaskPriority.NORMAL,
                   model_tier: Optional[str] = None,
                   difficulty: Optional[str] = None,
                   alert_mode: Optional[str] = None) -> BackgroundTask:
        task_id = str(uuid.uuid4())[:8]
        try:
            from core.jobs.model_router import infer_model_tier

            tier = infer_model_tier(task_type, difficulty, model_tier)
        except Exception:
            tier = model_tier or "small"
        task = BackgroundTask(
            id=task_id,
            name=name,
            description=description,
            conversation_id=conversation_id,
            conversation_name=conversation_name,
            original_query=original_query,
            task_type=task_type,
            kind=infer_kind(task_type, kind),
            priority=priority,
            model_tier=tier,
            difficulty=(difficulty or "normal"),
            alert_mode=normalize_alert_mode(alert_mode),
        )
        with self._tasks_lock:
            self._tasks[task_id] = task
            if conversation_id:
                self._conversation_tasks[conversation_id].append(task_id)
            self._persist_locked()
        return task

    def update_task(self, task_id: str, status: TaskStatus,
                    result: Any = None, error: str = None):
        with self._tasks_lock:
            if task_id in self._tasks:
                task = self._tasks[task_id]
                task.status = status
                if result is not None:
                    task.result = result
                if error is not None:
                    task.error = error
                if status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
                    task.completed_at = datetime.now()
                self._persist_locked()

    def bind_conversation(
        self, task_id: str, conversation_id: str, conversation_name: str = ""
    ) -> bool:
        """Attach an unbound (or rebindable) task to a conversation."""
        if not task_id or not conversation_id:
            return False
        with self._tasks_lock:
            task = self._tasks.get(task_id)
            if not task:
                return False
            old = (task.conversation_id or "").strip()
            if old and old != conversation_id:
                # Already bound elsewhere — leave alone.
                return False
            if old == conversation_id and (
                not conversation_name or task.conversation_name == conversation_name
            ):
                return True
            if old and old in self._conversation_tasks:
                try:
                    self._conversation_tasks[old].remove(task_id)
                except ValueError:
                    pass
            task.conversation_id = conversation_id
            if conversation_name:
                task.conversation_name = conversation_name
            self._conversation_tasks[conversation_id].append(task_id)
            self._persist_locked()
            return True

    def get_task(self, task_id: str) -> Optional[BackgroundTask]:
        with self._tasks_lock:
            return self._tasks.get(task_id)

    def get_running_tasks(self) -> List[BackgroundTask]:
        """Active work: running or blocked waiting on clarification."""
        with self._tasks_lock:
            return [
                t
                for t in self._tasks.values()
                if t.status in (TaskStatus.RUNNING, TaskStatus.NEEDS_INPUT)
            ]

    def get_tasks_for_conversation(self, conversation_id: str) -> List[BackgroundTask]:
        with self._tasks_lock:
            task_ids = self._conversation_tasks.get(conversation_id, [])
            return [self._tasks[tid] for tid in task_ids if tid in self._tasks]

    def get_completed_tasks_for_conversation(self, conversation_id: str) -> List[BackgroundTask]:
        with self._tasks_lock:
            tasks = self.get_tasks_for_conversation(conversation_id)
            return [t for t in tasks
                    if t.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED)
                    and t.notified_at is None]

    def mark_as_notified(self, task_id: str):
        """Record that a completion was delivered to the user/UI.

        Only sets ``notified_at``. Does **not** change status away from
        completed/failed — overwriting to NOTIFIED made manage_background_tasks
        result claim "no final result" even when result was stored.
        """
        with self._tasks_lock:
            if task_id in self._tasks:
                self._tasks[task_id].notified_at = datetime.now()
                self._persist_locked()

    def acknowledge_completion(
        self,
        task_id: str,
        *,
        reason: str = "handled",
        redact_history: bool = True,
    ) -> bool:
        """
        Mark a finished job as fully handled so AVA stops re-mentioning it.

        - Sets notified_at (no more DONE flushes)
        - metadata.handled / dismissed for short jobs (timers)
        - Acknowledges long-job inbox item when present
        - Rewrites DONE injects in live chat history so REQUIRED ACTION
          does not keep prompting the model on later turns
        """
        task_id = (task_id or "").strip()
        if not task_id:
            return False
        # Allow main:parentid markers → parent
        real_id = task_id
        if task_id.startswith("main:"):
            real_id = task_id.split(":", 1)[1].strip() or task_id

        with self._tasks_lock:
            task = self._tasks.get(real_id)
            if not task:
                return False
            now = datetime.now()
            if task.notified_at is None:
                task.notified_at = now
            task.metadata = dict(task.metadata or {})
            task.metadata["handled"] = True
            task.metadata["handled_at"] = now.isoformat()
            task.metadata["handled_reason"] = reason
            # Short jobs (timers etc.): fully dismissed — never sticky
            if task.kind == "short" or task.task_type == "timer":
                task.metadata["dismissed"] = True
            self._persist_locked()
            kind = task.kind
            ttype = task.task_type

        # Long-job inbox: ack so it drops from unread inject
        try:
            from core.jobs.inbox import get_inbox

            get_inbox().acknowledge(real_id)
        except Exception:
            pass

        if redact_history:
            try:
                from core.messageHandler import redact_done_injects

                redact_done_injects({real_id, task_id})
            except Exception as e:
                print(f"[TaskRegistry] redact after ack failed: {e}")

        print(
            f"[TaskRegistry] Acknowledged completion {real_id} "
            f"(kind={kind} type={ttype} reason={reason})"
        )
        return True

    def cancel_task(self, task_id: str) -> bool:
        with self._tasks_lock:
            if task_id not in self._tasks:
                return False
            task = self._tasks[task_id]
            if task.status not in (
                TaskStatus.RUNNING,
                TaskStatus.NEEDS_INPUT,
                TaskStatus.PENDING,
            ):
                return False
            task.status = TaskStatus.CANCELLED
            task.completed_at = datetime.now()
            task.pending_request_id = None
            self._persist_locked()
        # Cooperative cancel signal for live workers.
        try:
            from core.jobs.executor import get_executor

            get_executor().signal_cancel(task_id)
        except Exception as e:
            print(f"[TaskRegistry] Cancel event failed: {e}")
        # Unblock any clarification waiter outside the lock.
        try:
            from core.jobs.clarify import get_clarification_bridge

            get_clarification_bridge().cancel_for_job(task_id)
        except Exception as e:
            print(f"[TaskRegistry] Clarify cancel failed: {e}")
        return True

    def is_cancelled(self, task_id: str) -> bool:
        with self._tasks_lock:
            task = self._tasks.get(task_id)
            if task and task.status == TaskStatus.CANCELLED:
                return True
        try:
            from core.jobs.executor import get_executor

            if get_executor().is_cancel_signaled(task_id):
                return True
        except Exception:
            pass
        return False

    def get_pending_notifications(self) -> List[BackgroundTask]:
        with self._tasks_lock:
            completed = [t for t in self._tasks.values()
                        if t.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED)
                        and t.notified_at is None]
            return completed

    def get_all_tasks(self) -> List[BackgroundTask]:
        with self._tasks_lock:
            return list(self._tasks.values())

    def cleanup_old_tasks(self, max_age_hours: int = 24):
        with self._tasks_lock:
            cutoff = datetime.now() - timedelta(hours=max_age_hours)
            to_remove = []
            for task_id, task in self._tasks.items():
                if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.NOTIFIED, TaskStatus.CANCELLED):
                    if task.completed_at and task.completed_at < cutoff:
                        to_remove.append(task_id)
                        if task.conversation_id in self._conversation_tasks:
                            if task_id in self._conversation_tasks[task.conversation_id]:
                                self._conversation_tasks[task.conversation_id].remove(task_id)
            for task_id in to_remove:
                del self._tasks[task_id]
            if to_remove:
                self._persist_locked()

    def clear_all(self):
        with self._tasks_lock:
            self._tasks.clear()
            self._conversation_tasks.clear()
            self._persist_locked()


Task = BackgroundTask


# ============================================================================
# CONVERSATION STATE MANAGER
# ============================================================================

class ConversationStateManager:
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
        self._current_conversation_id: Optional[str] = None
        self._current_conversation_name: str = ""
        self._is_conversation_active: bool = False
        self._is_speaking: bool = False
        self._is_listening: bool = False
        self._last_user_activity: datetime = datetime.now()
        self._current_mode: str = "idle"
        self._initialized = True

    @property
    def current_conversation_id(self) -> Optional[str]:
        with self._lock:
            return self._current_conversation_id

    @current_conversation_id.setter
    def current_conversation_id(self, value: Optional[str]):
        with self._lock:
            self._current_conversation_id = value

    @property
    def current_conversation_name(self) -> str:
        with self._lock:
            return self._current_conversation_name

    @current_conversation_name.setter
    def current_conversation_name(self, value: str):
        with self._lock:
            self._current_conversation_name = value

    @property
    def is_conversation_active(self) -> bool:
        with self._lock:
            return self._is_conversation_active

    @is_conversation_active.setter
    def is_conversation_active(self, value: bool):
        with self._lock:
            self._is_conversation_active = value
            if value:
                self._last_user_activity = datetime.now()

    @property
    def is_speaking(self) -> bool:
        with self._lock:
            return self._is_speaking

    @is_speaking.setter
    def is_speaking(self, value: bool):
        with self._lock:
            self._is_speaking = value
            if value:
                self._last_user_activity = datetime.now()

    @property
    def is_listening(self) -> bool:
        with self._lock:
            return self._is_listening

    @is_listening.setter
    def is_listening(self, value: bool):
        with self._lock:
            self._is_listening = value

    @property
    def current_mode(self) -> str:
        with self._lock:
            return self._current_mode

    @current_mode.setter
    def current_mode(self, value: str):
        with self._lock:
            self._current_mode = value

    def conversation_started(self, conversation_id: str, conversation_name: str = ""):
        with self._lock:
            self._current_conversation_id = conversation_id
            self._current_conversation_name = conversation_name
            self._is_conversation_active = True
            self._last_user_activity = datetime.now()

    def conversation_ended(self):
        with self._lock:
            self._is_conversation_active = False

    def user_spoke(self):
        with self._lock:
            self._last_user_activity = datetime.now()
            self._is_conversation_active = True

    def assistant_spoke(self):
        with self._lock:
            self._last_user_activity = datetime.now()

    def get_idle_duration(self) -> timedelta:
        with self._lock:
            return datetime.now() - self._last_user_activity

    def is_user_idle(self, threshold_seconds: int = 30) -> bool:
        return self.get_idle_duration().total_seconds() > threshold_seconds

    def reset(self):
        with self._lock:
            self._current_conversation_id = None
            self._current_conversation_name = ""
            self._is_conversation_active = False
            self._is_speaking = False
            self._is_listening = False
            self._last_user_activity = datetime.now()
            self._current_mode = "idle"


# ============================================================================
# NOTIFICATION DECISION ENGINE
# ============================================================================

class NotificationDecisionEngine:
    def __init__(self):
        self.registry = TaskRegistry()
        self.state_manager = ConversationStateManager()

    def decide_notification_strategy(self, task: BackgroundTask) -> NotificationStrategy:
        """Pick how to surface a finished job.

        JARVIS rule: if the user is in an active chat, the main agent must
        speak a short update (NEXT_RESPONSE). Sticky inbox is the *offline*
        fallback, not a replacement for mid-chat announcement.

        Empty ``task.conversation_id`` is treated as belonging to the current
        chat (orphan attach) — never as a reason to stay silent.
        """
        is_active = self.state_manager.is_conversation_active
        # Urgent interrupt only for real alarms — not every timer (default silent).
        mode = normalize_alert_mode(getattr(task, "alert_mode", None))
        is_urgent = task.priority in (TaskPriority.HIGH, TaskPriority.URGENT) or (
            mode == "continuous"
        )


        # User is present → always wake main (same/different/empty origin).
        if is_active:
            return NotificationStrategy(
                delivery_method=DeliveryMethod.NEXT_RESPONSE,
                notification_text=task.to_compact_done(),
                interrupt_current=False,
                priority=TaskPriority.URGENT if is_urgent else task.priority,
                task_id=task.id,
            )

        # Away (idle / wake-word / no conversation): hard interrupt for timers.
        if is_urgent:
            return NotificationStrategy(
                delivery_method=DeliveryMethod.IMMEDIATE,
                notification_text=task.to_idle_notification(),
                interrupt_current=True,
                priority=TaskPriority.URGENT,
                task_id=task.id,
            )

        return NotificationStrategy(
            delivery_method=DeliveryMethod.QUEUE_FOR_IDLE,
            notification_text=task.to_idle_notification(),
            interrupt_current=False,
            priority=task.priority,
            task_id=task.id,
        )

    def _assess_conversation_relevance(self, current_conv_id: str, task: BackgroundTask) -> float:
        try:
            from knowledge.ConversationManager import get_manager
            mgr = get_manager()
            current_conv = mgr.current_conversation

            if not current_conv or not current_conv.messages:
                return 0.0

            task_keywords = set(task.original_query.lower().split())
            if not task_keywords:
                return 0.0

            conv_keywords = set()
            for msg in current_conv.messages[-5:]:
                if msg.get("role") == "user":
                    content = msg.get("content", "").lower()
                    conv_keywords.update(content.split())

            overlap = task_keywords.intersection(conv_keywords)
            return len(overlap) / len(task_keywords)

        except Exception:
            return 0.1


# ============================================================================
# COMPLETION QUEUE
# ============================================================================

class CompletionQueue:
    _instance = None
    _lock = threading.Lock()

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
        self._queue = queue.PriorityQueue()
        self._pending_lock = threading.Lock()
        self._pending_by_conversation: Dict[str, List[BackgroundTask]] = defaultdict(list)
        self._idle_notifications: List[BackgroundTask] = []
        self._deferred_notifications: List[BackgroundTask] = []
        self._initialized = True

    def push(self, task: BackgroundTask, priority_override: Optional[int] = None):
        if task.notified_at:
            return
        meta = getattr(task, "metadata", None) or {}
        if meta.get("handled") or meta.get("dismissed") or meta.get("announced"):
            return
        try:
            from core.jobs.announce_queue import get_announce_queue

            if get_announce_queue().was_announced(task.id):
                return
        except Exception:
            pass
        if priority_override:
            priority_map = {0: TaskPriority.URGENT, 1: TaskPriority.HIGH, 2: TaskPriority.NORMAL, 3: TaskPriority.LOW}
            task.priority = priority_map.get(priority_override, TaskPriority.NORMAL)
        priority_value = self._get_priority_value(task)
        # Use a tuple that won't cause comparison issues: (priority, timestamp, unique_id, task)
        # The unique_id ensures that tasks with same priority and timestamp can still be ordered
        unique_id = id(task)  # Unique object ID
        self._queue.put((priority_value, datetime.now(), unique_id, task))
        with self._pending_lock:
            if task.conversation_id:
                self._pending_by_conversation[task.conversation_id].append(task)

    def _get_priority_value(self, task: BackgroundTask) -> int:
        priority_map = {
            TaskPriority.URGENT: 0,
            TaskPriority.HIGH: 1,
            TaskPriority.NORMAL: 2,
            TaskPriority.LOW: 3
        }
        return priority_map.get(task.priority, 2)

    def pop(self, timeout: float = 0.1) -> Optional[BackgroundTask]:
        try:
            priority, timestamp, unique_id, task = self._queue.get(timeout=timeout)
            return task
        except queue.Empty:
            return None

    def pop_all(self) -> List[BackgroundTask]:
        tasks = []
        while True:
            task = self.pop(timeout=0.01)
            if task is None:
                break
            tasks.append(task)
        return tasks

    def get_notifications_for_conversation(self, conversation_id: str) -> List[BackgroundTask]:
        with self._pending_lock:
            notifications = self._pending_by_conversation.get(conversation_id, [])
            self._pending_by_conversation[conversation_id] = []
            return notifications

    def get_idle_notifications(self) -> List[BackgroundTask]:
        with self._pending_lock:
            notifications = list(self._idle_notifications)
            self._idle_notifications.clear()
            return notifications

    def get_deferred_notifications(self) -> List[BackgroundTask]:
        with self._pending_lock:
            notifications = list(self._deferred_notifications)
            self._deferred_notifications.clear()
            return notifications

    def store_for_conversation(self, task: BackgroundTask):
        with self._pending_lock:
            if task.conversation_id:
                self._pending_by_conversation[task.conversation_id].append(task)

    def store_for_idle(self, task: BackgroundTask):
        with self._pending_lock:
            self._idle_notifications.append(task)

    def store_deferred(self, task: BackgroundTask):
        with self._pending_lock:
            self._deferred_notifications.append(task)

    def has_notifications(self) -> bool:
        return not self._queue.empty()

    def has_pending_for_conversation(self, conversation_id: str) -> bool:
        with self._pending_lock:
            return len(self._pending_by_conversation.get(conversation_id, [])) > 0

    def has_idle_notifications(self) -> bool:
        with self._pending_lock:
            return len(self._idle_notifications) > 0

    def has_pending(self) -> bool:
        with self._pending_lock:
            has_conv = any(len(v) > 0 for v in self._pending_by_conversation.values())
            has_idle = len(self._idle_notifications) > 0
            has_deferred = len(self._deferred_notifications) > 0
            return has_conv or has_idle or has_deferred or not self._queue.empty()

    def store_pending(self, summary: str):
        task = BackgroundTask(
            id=str(uuid.uuid4())[:8],
            name="Legacy Task",
            description=summary,
            status=TaskStatus.COMPLETED,
            result=summary,
            conversation_id="legacy",
        )
        self.store_for_idle(task)

    def get_and_clear_pending(self) -> List[str]:
        with self._pending_lock:
            idle_tasks = list(self._idle_notifications)
            self._idle_notifications.clear()
            all_conv_tasks = []
            for conv_id in list(self._pending_by_conversation.keys()):
                all_conv_tasks.extend(self._pending_by_conversation[conv_id])
                self._pending_by_conversation[conv_id] = []
            deferred_tasks = list(self._deferred_notifications)
            self._deferred_notifications.clear()
            all_tasks = idle_tasks + all_conv_tasks + deferred_tasks
            return [t.to_summary() for t in all_tasks]


# ============================================================================
# SUB-AGENT
# ============================================================================

class SubAgent:
    def __init__(self, task_name: str, task_description: str = "",
                 conversation_id: str = "", conversation_name: str = "",
                 original_query: str = "", task_type: str = "general",
                 kind: Optional[str] = None,
                 priority: TaskPriority = TaskPriority.NORMAL,
                 notify_on_completion: bool = True,
                 model_tier: Optional[str] = None,
                 difficulty: Optional[str] = None,
                 alert_mode: Optional[str] = None):
        self.registry = TaskRegistry()
        self.completion_queue = CompletionQueue()
        self.state_manager = ConversationStateManager()

        if not conversation_id and self.state_manager.current_conversation_id:
            conversation_id = self.state_manager.current_conversation_id
        if not conversation_name and self.state_manager.current_conversation_name:
            conversation_name = self.state_manager.current_conversation_name
        if not original_query:
            original_query = task_description

        self.task = self.registry.create_task(
            name=task_name,
            description=task_description,
            conversation_id=conversation_id,
            conversation_name=conversation_name,
            original_query=original_query,
            task_type=task_type,
            kind=kind,
            priority=priority,
            model_tier=model_tier,
            difficulty=difficulty,
            alert_mode=alert_mode,
        )
        # Freeze origin conversation so later chats / clarifications still know why.
        try:
            from core.jobs.dossier import attach_origin_to_task

            attach_origin_to_task(self.task)
            with self.registry._tasks_lock:
                live = self.registry._tasks.get(self.task.id)
                if live is not None:
                    live.metadata = dict(self.task.metadata or {})
                    self.registry._persist_locked()
                    self.task = live
        except Exception as e:
            print(f"[TaskManager] origin dossier attach failed: {e}")
        self._thread: Optional[threading.Thread] = None
        self.notify_on_completion = notify_on_completion
        try:
            from core.jobs.executor import get_executor

            get_executor().cancel_event(self.task.id)
        except Exception:
            pass

    def is_cancelled(self) -> bool:
        return self.registry.is_cancelled(self.task.id)

    def ask_main(
        self,
        question: str,
        options: Optional[List[str]] = None,
        context_snippet: str = "",
        default_if_timeout: Optional[str] = None,
        require_user: bool = False,
        allow_main_self_answer: bool = True,
        timeout_seconds: Optional[int] = None,
    ) -> str:
        """
        Pause this worker and ask the main agent (or user) a short question.

        Only the answer string returns into the worker context — not full chat
        history. Blocks this thread only; other jobs keep running.
        """
        if self.is_cancelled():
            return default_if_timeout or "Clarification cancelled."

        from core.jobs.clarify import get_clarification_bridge

        bridge = get_clarification_bridge()
        live = self.registry.get_task(self.task.id)
        job_name = live.name if live else self.task.name
        job_kind = (live.kind if live else self.task.kind) or "long"

        # Enrich with origin pack so main agent knows what this job is building
        # even if the user has switched conversations.
        enriched_ctx = context_snippet or ""
        try:
            from core.jobs.dossier import enrich_clarification_context

            enriched_ctx = enrich_clarification_context(
                self.task.id, context_snippet or ""
            )
        except Exception:
            pass

        response = bridge.ask_main(
            job_id=self.task.id,
            question=question,
            options=options,
            context_snippet=enriched_ctx,
            default_if_timeout=default_if_timeout,
            require_user=require_user,
            allow_main_self_answer=allow_main_self_answer,
            timeout_seconds=timeout_seconds,
            job_name=job_name,
            job_kind=job_kind,
        )
        return response.answer or ""

    def execute(self, **kwargs) -> Any:
        raise NotImplementedError("Subclasses must implement execute()")

    def _run(self, **kwargs):
        try:
            self.registry.update_task(self.task.id, TaskStatus.RUNNING)
            result = self.execute(**kwargs)
            if not self.is_cancelled():
                self.registry.update_task(
                    self.task.id,
                    TaskStatus.COMPLETED,
                    result=result
                )
            completed_task = self.registry.get_task(self.task.id)
            if completed_task and self.notify_on_completion:
                # Only route terminal statuses; cancel during needs_input may
                # already be CANCELLED without a result path.
                if completed_task.status in (
                    TaskStatus.COMPLETED,
                    TaskStatus.FAILED,
                    TaskStatus.CANCELLED,
                ):
                    self._route_completion(completed_task)
        except Exception as e:
            if not self.is_cancelled():
                self.registry.update_task(
                    self.task.id,
                    TaskStatus.FAILED,
                    error=str(e)
                )
            completed_task = self.registry.get_task(self.task.id)
            if completed_task and self.notify_on_completion:
                if completed_task.status in (
                    TaskStatus.COMPLETED,
                    TaskStatus.FAILED,
                    TaskStatus.CANCELLED,
                ):
                    self._route_completion(completed_task)
        finally:
            self._thread = None

    def _route_completion(self, task: "BackgroundTask"):
        """Finish routing for a completed/failed job.

        - All top-level jobs: desktop toast (one-shot).
        - kind=long only: sticky unread inbox (survives until result/dismiss).
        - kind=short (incl. timers): no inbox — fire once, then quiet.
        - Push completion queue so response_loop can wake/speak when useful.
        Mission children set notify_on_completion=False and skip this entirely.
        """
        if task.status not in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
            return

        # If the job was started before a conversation was bound, stamp the
        # active chat so the decision engine can wake the right place.
        if not (task.conversation_id or "").strip():
            cur_id = self.state_manager.current_conversation_id or ""
            cur_name = self.state_manager.current_conversation_name or ""
            if cur_id:
                self.registry.bind_conversation(task.id, cur_id, cur_name)
                refreshed = self.registry.get_task(task.id)
                if refreshed:
                    task = refreshed

        # Chain consecutive work before notify/speak so DONE inject sees child ids.
        # Runs even after /new — binds the child to the active conversation if any.
        if task.status == TaskStatus.COMPLETED:
            try:
                spawn_follow_up_tasks(task)
                refreshed = self.registry.get_task(task.id)
                if refreshed:
                    task = refreshed
            except Exception as e:
                print(f"[TaskManager] follow_up spawn error: {e}")

        # Finalize held dossier (origin + result + artifacts + continue hints).
        # Survives after announce so main can still "run the program" later.
        try:
            from core.jobs.dossier import finalize_dossier

            finalize_dossier(task)
            refreshed = self.registry.get_task(task.id)
            if refreshed:
                # Re-copy metadata dossier_brief if finalize updated live task via id
                task = refreshed
            # ensure metadata from finalize is on live row
            with self.registry._tasks_lock:
                live = self.registry._tasks.get(task.id)
                if live is not None and getattr(task, "metadata", None):
                    # merge announce_policy / dossier_brief
                    brief = (task.metadata or {}).get("dossier_brief")
                    if brief:
                        live.metadata["dossier_brief"] = brief
                    if (task.metadata or {}).get("announce_policy"):
                        live.metadata["announce_policy"] = task.metadata["announce_policy"]
                    self.registry._persist_locked()
                    task = live
        except Exception as e:
            print(f"[TaskManager] finalize_dossier failed: {e}")

        self._desktop_notify(task)

        if task.kind == "long" and task.status in (
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
        ):
            body = str(task.result or task.error or "No result.")
            summary = body[:500]
            artifact = getattr(task, "artifact_path", None)
            try:
                from core.jobs.context import summary_for_inbox, write_artifact

                path = write_artifact(task.id, body, title=task.name)
                if path:
                    artifact = path
                    with self.registry._tasks_lock:
                        live = self.registry._tasks.get(task.id)
                        if live:
                            live.artifact_path = path
                            self.registry._persist_locked()
                            task = live
                summary = summary_for_inbox(body)
            except Exception as e:
                print(f"[TaskManager] Artifact write skipped: {e}")

            try:
                from core.jobs.inbox import get_inbox

                get_inbox().add(
                    id=task.id,
                    source="job",
                    title=task.name,
                    summary=summary,
                    artifact_path=artifact,
                    severity="normal",
                )
            except Exception as e:
                print(f"[TaskManager] Could not create inbox item: {e}")

        self.completion_queue.push(task)

    @staticmethod
    def _desktop_notify(task: "BackgroundTask"):
        """Optional toast only — no auto-beep. Silent mode skips entirely."""
        try:
            mode = normalize_alert_mode(getattr(task, "alert_mode", None))
            if not alert_mode_wants_toast(mode):
                print(
                    f"[TaskManager] Skip desktop toast for {task.id} "
                    f"(alert_mode={mode}; main agent may ping/notify later)"
                )
                return
            from core.jobs.notify import desktop_notify

            if task.task_type == "timer" and task.status == TaskStatus.COMPLETED:
                title = "AVA — Timer"
                body = str(task.result or task.description or "Timer complete")[:160]
            elif task.status == TaskStatus.COMPLETED:
                title = "AVA — Task done"
                body = f"{task.name}: {str(task.result or '')[:140]}"
            else:
                title = "AVA — Task failed"
                body = f"{task.name}: {str(task.error or '')[:140]}"
            # Toast without system beep — main agent uses ping for sound.
            desktop_notify(title, body, sound=False)
        except Exception as e:
            print(f"[TaskManager] Desktop notify failed: {e}")

    def start(self, **kwargs) -> str:
        """Start via JobExecutor (concurrency cap + cancel event + live registry)."""
        slot_timeout = float(kwargs.pop("_slot_timeout", 2.0))
        try:
            from core.jobs.executor import get_executor

            ex = get_executor()
            ok = ex.start_job(
                self.task.id,
                self._run,
                kwargs=kwargs,
                acquire_timeout=slot_timeout,
            )
            if not ok:
                self.registry.update_task(
                    self.task.id,
                    TaskStatus.FAILED,
                    error=(
                        "Could not start: background job concurrency limit reached "
                        f"(max {ex.max_jobs}). Try again shortly."
                    ),
                )
                if self.notify_on_completion:
                    failed = self.registry.get_task(self.task.id)
                    if failed:
                        self._route_completion(failed)
                return self.task.id
            with ex._lock:
                self._thread = ex._live_threads.get(self.task.id)
            return self.task.id
        except Exception as e:
            print(f"[TaskManager] Executor start failed, falling back: {e}")
            self._thread = threading.Thread(
                target=self._run,
                kwargs=kwargs,
                daemon=True,
            )
            self._thread.start()
            return self.task.id

    def is_running(self) -> bool:
        try:
            from core.jobs.executor import get_executor

            if get_executor().is_live(self.task.id):
                return True
        except Exception:
            pass
        return self._thread is not None and self._thread.is_alive()


# ============================================================================
# CONCRETE SUB-AGENTS
# ============================================================================

class ResearchAgent(SubAgent):
    def __init__(self, topic: str, conversation_id: str = "",
                 conversation_name: str = "", kind: Optional[str] = None,
                 priority: TaskPriority = TaskPriority.NORMAL,
                 model_tier: Optional[str] = None,
                 difficulty: Optional[str] = None,
                 level: Optional[int] = None):
        # Research depth 1 (quick) … 5 (exhaustive). Default 3.
        try:
            lvl = int(level) if level is not None else 3
        except (TypeError, ValueError):
            lvl = 3
        lvl = max(1, min(5, lvl))
        super().__init__(
            task_name=f"Research L{lvl}: {topic}",
            task_description=f"Level {lvl} research on {topic}",
            conversation_id=conversation_id,
            conversation_name=conversation_name,
            original_query=topic,
            task_type="research",
            kind=kind,
            priority=priority,
            model_tier=model_tier,
            difficulty=difficulty,
        )
        self.topic = topic
        self.level = lvl

    def execute(self, **kwargs) -> str:
        from functions.web.deep_research import research

        # Origin conversation is frozen on the dossier (for main agent later and
        # clarifications). Research API uses topic + level; full origin rides the dossier.
        result = research(self.topic, level=self.level)
        return result


class WebScrapingAgent(SubAgent):
    def __init__(self, url: str, purpose: str = "data extraction",
                 conversation_id: str = "", conversation_name: str = "",
                 kind: Optional[str] = None):
        super().__init__(
            task_name=f"Web Scraping",
            task_description=f"Fetching data from {url} for {purpose}",
            conversation_id=conversation_id,
            conversation_name=conversation_name,
            original_query=f"Scrape {url} for {purpose}",
            task_type="web_scrape",
            kind=kind,
        )
        self.url = url

    def execute(self, **kwargs) -> str:
        from functions.web.internet import fetch_website_data
        result = fetch_website_data(url=self.url)
        return result


class TimerAgent(SubAgent):
    """Countdown that survives conversation changes; fires once (no sticky inbox)."""

    def __init__(self, duration_seconds: int, message: str = "Timer complete",
                 conversation_id: str = "", conversation_name: str = "",
                 kind: Optional[str] = None,
                 alert_mode: Optional[str] = None):
        # Timers are always one-shot for inbox purposes. Override to long is
        # ignored so they never nag every new conversation after firing.
        # alert_mode is chosen by the main LLM: continuous for real alarms,
        # notify for casual reminders.
        mode = normalize_alert_mode(alert_mode)
        super().__init__(
            task_name=f"Timer ({duration_seconds}s)",
            task_description=message,
            conversation_id=conversation_id,
            conversation_name=conversation_name,
            original_query=message,
            task_type="timer",
            kind="short",
            priority=(
                TaskPriority.URGENT if mode == "continuous" else TaskPriority.NORMAL
            ),
            alert_mode=mode,
        )
        self.duration = max(0, int(duration_seconds) if duration_seconds is not None else 0)
        self.message = message

    def execute(self, **kwargs) -> str:
        # Cooperative cancel: wake every second instead of one long sleep.
        end = time.time() + self.duration
        # Expose countdown metadata for TUI / status UIs
        try:
            with self.registry._tasks_lock:
                live = self.registry._tasks.get(self.task.id)
                if live is not None:
                    live.metadata["duration_seconds"] = self.duration
                    live.metadata["started_at"] = time.time()
                    live.metadata["ends_at"] = end
                    live.metadata["timer_message"] = self.message
        except Exception:
            pass
        while time.time() < end:
            if self.is_cancelled():
                return "Timer cancelled."
            time.sleep(min(1.0, max(0.0, end - time.time())))
        return self.message


class SmartHomeAgent(SubAgent):
    """Short job: exclusive smarthome lane so device batches never interleave."""

    def __init__(self, command: str, conversation_id: str = "",
                 conversation_name: str = "", kind: Optional[str] = None):
        super().__init__(
            task_name=f"SmartHome: {command[:20]}...",
            task_description=f"Executing smart home command: {command}",
            conversation_id=conversation_id,
            conversation_name=conversation_name,
            original_query=command,
            task_type="smarthome",
            # Short by default: one-shot notify, never sticky inbox.
            kind=kind if kind in ("short", "long") else "short",
        )
        self.command = command

    def execute(self, **kwargs) -> str:
        from core.jobs.resources import get_lane
        from functions.system.smart_home_agent import run_smarthome_agent

        lane = get_lane("smarthome")
        timeout = float(kwargs.get("lane_timeout", 120))
        if not lane.acquire(self.task.id, timeout=timeout):
            return (
                "Could not acquire exclusive smarthome lane "
                f"(held by {lane.holder()}). Try again shortly."
            )
        try:
            if self.is_cancelled():
                return "Smart home task cancelled before execution."
            return run_smarthome_agent(self.command)
        finally:
            lane.release(self.task.id)


class DemoAgent(SubAgent):
    """
    Deterministic timed background job for testing (no LLM, no network).

    Burns real wall-clock time so you can chat, start a new conversation, or
    sleep while it runs. Optionally pauses mid-way via ask_main to exercise
    the clarification bridge.

    Defaults to kind=long → sticky inbox on complete.
    """

    def __init__(
        self,
        duration: int = 20,
        topic: str = "demo work",
        ask: bool = False,
        question: str = "",
        require_user: bool = True,
        default_if_timeout: str = "default-choice",
        conversation_id: str = "",
        conversation_name: str = "",
        kind: Optional[str] = None,
        message: str = "",
    ):
        duration = max(2, min(int(duration or 20), 600))
        topic = (topic or message or "demo work").strip() or "demo work"
        super().__init__(
            task_name=f"Demo: {topic[:40]}",
            task_description=f"Simulated work for {duration}s on: {topic}",
            conversation_id=conversation_id,
            conversation_name=conversation_name,
            original_query=topic,
            task_type="demo",
            kind=kind if kind in ("short", "long") else "long",
            priority=TaskPriority.NORMAL,
        )
        self.duration = duration
        self.topic = topic
        self.ask = bool(ask)
        self.question = (
            question
            or f"Demo needs a preference for '{topic}' — which option?"
        )
        self.require_user = bool(require_user)
        self.default_if_timeout = default_if_timeout or "default-choice"

    def _sleep_cancellable(self, seconds: float) -> bool:
        """Sleep up to ``seconds``; return False if cancelled."""
        end = time.time() + max(0.0, seconds)
        while time.time() < end:
            if self.is_cancelled():
                return False
            time.sleep(min(0.25, max(0.0, end - time.time())))
        return not self.is_cancelled()

    def execute(self, **kwargs) -> str:
        # Split time: work → optional clarify → work remainder.
        if self.ask:
            first = max(1.0, self.duration * 0.35)
            rest = max(1.0, self.duration - first)
        else:
            first = float(self.duration)
            rest = 0.0

        if not self._sleep_cancellable(first):
            return f"Demo cancelled while working on '{self.topic}'."

        answer = None
        if self.ask:
            answer = self.ask_main(
                question=self.question,
                options=["option-a", "option-b", "flexible"],
                context_snippet=f"Demo mid-run on: {self.topic}",
                default_if_timeout=self.default_if_timeout,
                require_user=self.require_user,
                timeout_seconds=max(60, self.duration * 10),
            )
            if self.is_cancelled():
                return (
                    f"Demo cancelled after clarification on '{self.topic}'. "
                    f"Last answer: {answer}"
                )
            if rest > 0 and not self._sleep_cancellable(rest):
                return (
                    f"Demo cancelled after answer on '{self.topic}'. "
                    f"Answer was: {answer}"
                )

        parts = [
            f"Demo finished: {self.topic}",
            f"ran {self.duration}s",
        ]
        if self.ask:
            parts.append(f"clarification answer={answer!r}")
        return ". ".join(parts) + "."


def _resolve_opencode_objective(*candidates: Any) -> str:
    """
    Build a non-empty OpenCode instruction from any spawn fields the main agent sent.

    Weak models often pass only title/session_id and omit objective — that used to
    crash with "opencode prompt is empty".
    """
    for c in candidates:
        if c is None:
            continue
        s = str(c).strip()
        if not s:
            continue
        # Placeholder names the SubAgent used when objective was blank
        if s.lower() in ("opencode task", "opencode", "coding", "task"):
            continue
        return s
    return ""


def _latest_user_text_from_messages(limit_chars: int = 500) -> str:
    """Last non-empty user turn from live chat (for empty-objective recovery)."""
    try:
        from core.messageHandler import messages, messages_lock

        with messages_lock:
            for msg in reversed(list(messages)):
                if (msg.get("role") or "").lower() != "user":
                    continue
                content = msg.get("content")
                if isinstance(content, list):
                    parts = []
                    for p in content:
                        if isinstance(p, dict) and p.get("type") == "text":
                            parts.append(str(p.get("text") or ""))
                        elif isinstance(p, str):
                            parts.append(p)
                    text = " ".join(parts).strip()
                else:
                    text = str(content or "").strip()
                if text:
                    return text[:limit_chars]
    except Exception:
        pass
    return ""


class OpenCodeAgent(SubAgent):
    """
    Background OpenCode coding session (uses installed ``opencode`` CLI).

    - Uses OpenCode's default model (does not force AVA's main model).
    - Stores session_id + description for later continue.
    - permissions: auto (default, --auto) | deny | ask (main agent yes/no then auto).
    - Always resolves a non-empty objective (title/description/user message fallbacks).
    """

    def __init__(
        self,
        objective: str = "",
        prompt: str = "",
        directory: str = "",
        session_id: str = "",
        title: str = "",
        description: str = "",
        permissions: str = "auto",
        continue_session: bool = False,
        model: str = "",
        context: str = "",
        conversation_id: str = "",
        conversation_name: str = "",
        kind: Optional[str] = None,
        topic: str = "",
        command: str = "",
        message: str = "",
    ):
        title = (title or "").strip()
        description = (description or "").strip()
        obj = _resolve_opencode_objective(
            objective,
            prompt,
            topic,
            command,
            message,
            description,
            title,
        )
        # Last-chance: latest user utterance in this chat
        if not obj:
            obj = _resolve_opencode_objective(_latest_user_text_from_messages())
        if not obj and session_id:
            obj = (
                "Continue this OpenCode session: apply the user's latest request "
                "to the project in the working directory. Use tools; do not only greet."
            )
        if not obj:
            obj = (
                "Complete the coding task the user just asked for in this chat. "
                "Use tools to write/edit files; do not only greet."
            )

        label = title or description or obj
        super().__init__(
            task_name=f"OpenCode: {label[:40]}",
            task_description=obj,
            conversation_id=conversation_id,
            conversation_name=conversation_name,
            original_query=obj,
            task_type="opencode",
            kind=kind if kind in ("short", "long") else "long",
            model_tier="none",
        )
        self.objective = obj
        self.directory = directory
        self.session_id = (session_id or "").strip()
        self.title = (title or description or obj[:80]).strip()
        self.description = (description or title or obj[:160]).strip()
        self.permissions = (permissions or "auto").strip().lower()
        self.continue_session = bool(continue_session)
        self.model = (model or "").strip()  # empty = OpenCode default
        self.context = (context or "").strip()

    def execute(self, **kwargs) -> str:
        from functions.system.opencode_client import run_opencode, resolve_directory
        from core.jobs.dossier import origin_context_for_worker

        # Re-resolve if somehow still blank mid-run
        if not (self.objective or "").strip():
            self.objective = _resolve_opencode_objective(
                _latest_user_text_from_messages(),
                self.title,
                self.description,
                "Continue OpenCode work on the project; apply the latest user request.",
            )

        # New builds: default to Desktop/AVA_OpenCode/<slug>, never the AVA repo.
        # Continues: use provided directory or leave empty → Desktop.
        create_project = not bool(self.session_id) and not bool(self.continue_session)
        directory = resolve_directory(
            self.directory,
            title=self.title or self.description,
            description=self.description or self.objective,
            create_project=create_project and not (self.directory or "").strip(),
        )
        live = self.registry.get_task(self.task.id) or self.task

        # Permission gate: ask main (or user via main) before granting --auto
        perm = self.permissions
        if perm in ("ask", "ask_main", "confirm"):
            answer = self.ask_main(
                question=(
                    f"OpenCode wants to run tools (edit/bash/write) in {directory}. "
                    f"Objective: {self.objective[:300]}. Allow full tool permissions?"
                ),
                options=["yes-auto", "no-safe", "cancel"],
                context_snippet=(
                    f"OpenCode job {self.task.id}. "
                    f"Session continue={self.session_id or self.continue_session}. "
                    "yes-auto = --auto; no-safe = reject risky tools; cancel = abort."
                ),
                default_if_timeout="no-safe",
                require_user=False,
                allow_main_self_answer=True,
                timeout_seconds=300,
            )
            ans = (answer or "").strip().lower()
            if "cancel" in ans:
                return "OpenCode cancelled: permission denied by main/user."
            if ans.startswith("y") or "auto" in ans or "allow" in ans or "yes" in ans:
                perm = "auto"
            else:
                perm = "deny"

        origin = ""
        try:
            origin = origin_context_for_worker(live)
        except Exception:
            origin = ""
        # If objective was a generic continue stub, prefer origin user request
        if origin and (
            not self.objective
            or self.objective.startswith("Continue this OpenCode")
            or self.objective.startswith("Complete the coding task")
        ):
            try:
                from core.jobs.dossier import get_dossier_for_task

                d = get_dossier_for_task(live)
                if d and (d.origin_user_request or "").strip():
                    self.objective = d.origin_user_request.strip()[:800]
            except Exception:
                pass
        # Keep origin short — full pack lives on dossier; OpenCode gets a clip via AVA_TASK.md
        if origin and len(origin) > 1200:
            origin = origin[:1200] + "..."

        # Default permissions to auto so tools work headless.
        # Main agent can pass deny/ask; ask is resolved above.
        if perm not in ("auto", "deny", "safe", "allow", "ask_granted"):
            perm = "auto"

        # Live metadata for TUI / dossier
        try:
            with self.registry._tasks_lock:
                t = self.registry._tasks.get(self.task.id)
                if t is not None:
                    t.metadata["opencode_directory"] = directory
                    t.metadata["opencode_session_id"] = self.session_id
                    t.metadata["opencode_permissions"] = perm
                    t.metadata["cwd"] = directory
                    self.registry._persist_locked()
        except Exception:
            pass

        # Objective only here — run_opencode writes AVA_TASK.md + short CLI message
        # (avoids Windows 8191-char argv truncation that caused idle "ready" replies).
        result = run_opencode(
            self.objective,
            directory=directory,
            session_id=self.session_id,
            title=self.title or f"AVA {self.task.id}",
            permissions=perm,
            continue_last=bool(self.continue_session) and not self.session_id,
            job_id=self.task.id,
            description=self.description,
            model=self.model,  # empty → OpenCode default
            origin=origin,
            context=self.context,
            is_cancelled=self.is_cancelled,
        )

        sid = (result.get("session_id") or "").strip()
        if sid:
            try:
                with self.registry._tasks_lock:
                    t = self.registry._tasks.get(self.task.id)
                    if t is not None:
                        t.metadata["opencode_session_id"] = sid
                        t.metadata["session_id"] = sid
                        t.metadata["cwd"] = result.get("directory") or directory
                        t.metadata["project_path"] = result.get("directory") or directory
                        t.metadata["continue_hints"] = [
                            f"Continue OpenCode: session_id={sid}",
                            f"cwd: {result.get('directory') or directory}",
                        ]
                        self.registry._persist_locked()
            except Exception:
                pass

        # Package result for dossier / DONE — cleaned summary (no monologue)
        from core.jobs.context import clean_outcome_summary

        raw_summary = result.get("summary") or result.get("content") or ""
        payload = {
            "summary": clean_outcome_summary(str(raw_summary), 280),
            "session_id": sid,
            "directory": result.get("directory") or directory,
            "status": result.get("status"),
            "tools": result.get("tools") or [],
            # Prefer concrete file path if tools mention one; else workdir
            "report_path": result.get("directory") or directory,
        }
        # Pull first written file path from tool lines if present
        for tline in payload["tools"]:
            s = str(tline)
            if "\\" in s or "/" in s:
                # e.g. write: completed Users\...\file.py
                parts = s.replace("completed", "").split()
                for p in reversed(parts):
                    if p.endswith((".py", ".js", ".ts", ".md", ".txt", ".html")):
                        # Normalize partial Windows path
                        if not p.startswith("C:") and "Users" in p:
                            payload["report_path"] = "C:\\" + p if not p.startswith("\\") else p
                        else:
                            payload["report_path"] = p
                        break
        if result.get("status") == "cancelled":
            return json.dumps(payload, ensure_ascii=False)
        if result.get("status") != "success":
            payload["error"] = clean_outcome_summary(
                str(result.get("content") or "failed"), 200
            )
            return json.dumps(payload, ensure_ascii=False)
        return json.dumps(payload, ensure_ascii=False)


class DelegatedAIAgent(SubAgent):
    """General-purpose background AI worker for open-ended delegated tasks."""

    def __init__(self, objective: str, context: str = "",
                 expected_output: str = "A concise final result",
                 conversation_id: str = "", conversation_name: str = "",
                 kind: Optional[str] = None,
                 priority: TaskPriority = TaskPriority.NORMAL,
                 model_tier: Optional[str] = None,
                 difficulty: Optional[str] = None):
        super().__init__(
            task_name=f"Delegated agent: {objective[:40]}",
            task_description=objective,
            conversation_id=conversation_id,
            conversation_name=conversation_name,
            original_query=objective,
            task_type="delegate",
            kind=kind,
            priority=priority,
            model_tier=model_tier,
            difficulty=difficulty,
        )
        self.objective = objective
        self.context = context
        self.expected_output = expected_output

    def execute(self, **kwargs) -> str:
        from core.server_api import generate_remote_response
        from core.tool_definitions import tools
        from core.tool_registry import init_plugins, registry
        from core.FuncHandler import handle_tool_call
        from core.jobs.context import compact_worker_user_pack, worker_system_prompt

        # Dangerous / recursive ops only. Plugins (e.g. whatsapp_plugin) ARE allowed —
        # consecutive timer→message jobs need them. Main chat tools + plugins.
        blocked_tools = {
            "background_task",
            "get_background_tasks_status",
            "manage_background_tasks",
            "make_phone_call",
            "shutdown_pc",
            "delete_file",
            "bash",
            "ping",
        }
        init_plugins()
        merged = list(tools) + list(registry.get_schema() or [])
        worker_tools = [
            tool
            for tool in merged
            if tool.get("function", {}).get("name") not in blocked_tools
        ]
        # Worker-only bridge: pause and ask main/user for user-specific decisions.
        worker_tools = list(worker_tools) + [REQUEST_CLARIFICATION_TOOL]

        plugin_names = sorted(
            n
            for n in (registry.all_tools() or {})
            if n not in blocked_tools
        )
        plugin_hint = ""
        if plugin_names:
            plugin_hint = (
                "Plugin tools available (use these by exact name): "
                + ", ".join(plugin_names)
                + ". For WhatsApp: use whatsapp_plugin "
                "(search_contacts then send_message)."
            )

        live = self.registry.get_task(self.task.id) or self.task
        tier = getattr(live, "model_tier", None) or "small"

        # Merge spawn-time origin conversation into worker context
        worker_ctx = (self.context or "").strip()
        try:
            from core.jobs.dossier import origin_context_for_worker

            origin = origin_context_for_worker(live)
            if origin:
                worker_ctx = (
                    f"{worker_ctx}\n\n--- Origin conversation ---\n{origin}".strip()
                    if worker_ctx
                    else origin
                )
        except Exception:
            pass
        if not worker_ctx:
            worker_ctx = "No additional context provided."

        messages = [
            {
                "role": "system",
                "content": worker_system_prompt(plugin_hint),
            },
            {
                "role": "user",
                "content": compact_worker_user_pack(
                    self.objective,
                    worker_ctx,
                    self.expected_output,
                ),
            },
        ]

        final_content = ""
        for _ in range(8):
            if self.is_cancelled():
                return "Delegated agent was cancelled."
            response = generate_remote_response(
                messages,
                tools=worker_tools,
                model_tier=tier,
                include_server_context=False,
            )
            content = response.get("content")
            tool_calls = response.get("tool_calls") or []

            assistant_msg = {"role": "assistant"}
            if content:
                assistant_msg["content"] = content
                final_content = content
            if tool_calls:
                assistant_msg["tool_calls"] = tool_calls
            messages.append(assistant_msg)

            if not tool_calls:
                return final_content or "Delegated agent completed without a text result."

            for tool_call in tool_calls:
                if self.is_cancelled():
                    return "Delegated agent was cancelled."

                # Resolve function name for special worker tools
                if isinstance(tool_call, dict):
                    function_payload = tool_call.get("function", {}) or {}
                    func_name = function_payload.get("name")
                    args_raw = function_payload.get("arguments", "{}")
                    tool_id = tool_call.get("id") or "unknown"
                else:
                    func_name = tool_call.function.name
                    args_raw = tool_call.function.arguments
                    tool_id = getattr(tool_call, "id", None) or "unknown"

                if isinstance(args_raw, str):
                    try:
                        args = json.loads(args_raw) if args_raw.strip() else {}
                    except json.JSONDecodeError:
                        args = {}
                elif isinstance(args_raw, dict):
                    args = args_raw
                else:
                    args = {}

                if func_name == "request_clarification":
                    result = self.ask_main(
                        question=str(args.get("question", "")),
                        options=args.get("options"),
                        context_snippet=str(args.get("context_snippet", "") or ""),
                        default_if_timeout=args.get("default_if_timeout"),
                        require_user=bool(args.get("require_user", False)),
                    )
                    if self.is_cancelled():
                        return "Delegated agent was cancelled."
                else:
                    result, tool_id = handle_tool_call(tool_call)

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_id or "unknown",
                        "content": result if isinstance(result, str) else json.dumps(result),
                    }
                )

        return final_content or "Delegated agent reached its tool-iteration limit."


# Tool schema available only to background workers (not main agent tool list).
REQUEST_CLARIFICATION_TOOL = {
    "type": "function",
    "function": {
        "name": "request_clarification",
        "description": (
            "Pause this background job and ask the main agent or user a short "
            "question when you need a user-specific preference, constraint, or "
            "irreversible choice not available in the objective/context. "
            "Do not use for facts you can look up yourself."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "Short voice-safe question.",
                },
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional multiple-choice options.",
                },
                "context_snippet": {
                    "type": "string",
                    "description": "Why you are blocked (max ~300 chars).",
                },
                "default_if_timeout": {
                    "type": "string",
                    "description": "Safe default if no answer arrives in time.",
                },
                "require_user": {
                    "type": "boolean",
                    "description": "If true, main must ask the user (not self-answer).",
                },
            },
            "required": ["question"],
        },
    },
}

class MissionAgent(SubAgent):
    """Supervisor that decomposes a mission into parallel background workers."""

    def __init__(self, mission: str, context: str = "", max_workers: int = 3,
                 conversation_id: str = "", conversation_name: str = "",
                 kind: Optional[str] = None,
                 priority: TaskPriority = TaskPriority.NORMAL):
        super().__init__(
            task_name=f"Mission: {mission[:45]}",
            task_description=mission,
            conversation_id=conversation_id,
            conversation_name=conversation_name,
            original_query=mission,
            task_type="mission",
            kind=kind,
            priority=priority,
        )
        self.mission = mission
        self.context = context
        self.max_workers = max(1, min(int(max_workers or 3), 5))

    def _plan(self) -> List[Dict[str, str]]:
        from core.server_api import generate_remote_response

        messages = [
            {
                "role": "system",
                "content": (
                    "You plan work for background AI workers. Return only JSON: "
                    "{\"tasks\":[{\"objective\":\"...\",\"expected_output\":\"...\"}]}. "
                    "Create independent tasks that can run in parallel. Do not include more tasks "
                    "than requested."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Mission:\n{self.mission}\n\nContext:\n{self.context}\n\n"
                    f"Maximum tasks: {self.max_workers}"
                ),
            },
        ]
        response = generate_remote_response(
            messages,
            tools=[],
            model_tier=getattr(self.task, "model_tier", None) or "main",
            include_server_context=False,
        )
        raw = response.get("content") or ""
        try:
            data = json.loads(raw)
            tasks = data.get("tasks", [])
        except Exception:
            tasks = []
        planned = []
        for item in tasks[: self.max_workers]:
            objective = str(item.get("objective", "")).strip()
            if objective:
                planned.append(
                    {
                        "objective": objective,
                        "expected_output": str(item.get("expected_output", "A useful result")).strip(),
                    }
                )
        if not planned:
            planned.append(
                {
                    "objective": self.mission,
                    "expected_output": "Complete the mission and report the useful result.",
                }
            )
        return planned

    def _synthesize(self, child_results: List[Dict[str, str]]) -> str:
        from core.server_api import generate_remote_response

        messages = [
            {
                "role": "system",
                "content": (
                    "You are AVA's mission supervisor. Synthesize child worker results "
                    "into a concise final report. Lead with the outcome, then mention "
                    "important details, limitations, and any file paths."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "mission": self.mission,
                        "context": self.context,
                        "child_results": child_results,
                    },
                    indent=2,
                ),
            },
        ]
        response = generate_remote_response(
            messages,
            tools=[],
            model_tier=getattr(self.task, "model_tier", None) or "main",
            include_server_context=False,
        )
        return response.get("content") or json.dumps(child_results, indent=2)

    def execute(self, **kwargs) -> str:
        planned_tasks = self._plan()
        children: List[DelegatedAIAgent] = []
        for item in planned_tasks:
            if self.is_cancelled():
                return "Mission was cancelled before all workers started."
            child = DelegatedAIAgent(
                item["objective"],
                context=f"Parent mission: {self.mission}\n\nShared context:\n{self.context}",
                expected_output=item["expected_output"],
                conversation_id=self.task.conversation_id,
                conversation_name=self.task.conversation_name,
                priority=self.task.priority,
            )
            child.notify_on_completion = False
            child.start()
            children.append(child)

        deadline = time.time() + float(kwargs.get("timeout", 900))
        while time.time() < deadline:
            if self.is_cancelled():
                for child in children:
                    self.registry.cancel_task(child.task.id)
                return "Mission was cancelled."
            if all(not child.is_running() for child in children):
                break
            time.sleep(0.25)

        child_results = []
        for child in children:
            task = self.registry.get_task(child.task.id)
            if not task:
                continue
            child_results.append(
                {
                    "task_id": task.id,
                    "objective": task.original_query,
                    "status": task.status.value,
                    "result": str(task.result or task.error or ""),
                }
            )

        return self._synthesize(child_results)


# ============================================================================
# INTEGRATION HELPER FUNCTIONS
# ============================================================================

def dispatch_background_task(task_type: str, conversation_id: str = "",
                           conversation_name: str = "", **kwargs) -> tuple:
    state_manager = ConversationStateManager()
    if not conversation_id and state_manager.current_conversation_id:
        conversation_id = state_manager.current_conversation_id
    if not conversation_name and state_manager.current_conversation_name:
        conversation_name = state_manager.current_conversation_name

    def _as_bool(value, default=False):
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        s = str(value).strip().lower()
        if s in ("1", "true", "yes", "y", "on"):
            return True
        if s in ("0", "false", "no", "n", "off", ""):
            return False
        return default

    difficulty = kwargs.get("difficulty")
    model_tier = kwargs.get("model_tier")

    # follow_up + internal chain metadata (not agent ctor kwargs)
    follow_up_raw = kwargs.pop("follow_up", None)
    follow_up_depth = kwargs.pop("_follow_up_depth", None)
    follow_up_parent = kwargs.pop("_follow_up_parent_id", None)
    # Also accept aliases some models invent
    if follow_up_raw is None:
        follow_up_raw = kwargs.pop("then", None) or kwargs.pop("next_task", None)

    agents = {
        "research": lambda: ResearchAgent(
            kwargs.get("topic", ""),
            conversation_id=conversation_id,
            conversation_name=conversation_name,
            kind=kwargs.get("kind"),
            model_tier=model_tier,
            difficulty=difficulty,
            level=kwargs.get("level") or kwargs.get("research_level"),
        ).start(),
        "scrape": lambda: WebScrapingAgent(
            kwargs.get("url", ""),
            kwargs.get("purpose", ""),
            conversation_id=conversation_id,
            conversation_name=conversation_name,
            kind=kwargs.get("kind"),
        ).start(),
        "timer": lambda: TimerAgent(
            kwargs.get("duration", 60),
            kwargs.get("message", "Timer complete"),
            conversation_id=conversation_id,
            conversation_name=conversation_name,
            kind=kwargs.get("kind"),
            alert_mode=kwargs.get("alert_mode"),
        ).start(),
        "smarthome": lambda: SmartHomeAgent(
            kwargs.get("command", ""),
            conversation_id=conversation_id,
            conversation_name=conversation_name,
            kind=kwargs.get("kind"),
        ).start(),
        "delegate": lambda: DelegatedAIAgent(
            kwargs.get("objective", "") or kwargs.get("topic", "") or kwargs.get("command", ""),
            context=kwargs.get("context", ""),
            expected_output=kwargs.get("expected_output", "A concise final result"),
            conversation_id=conversation_id,
            conversation_name=conversation_name,
            kind=kwargs.get("kind"),
            priority=TaskPriority.NORMAL,
            model_tier=model_tier,
            difficulty=difficulty,
        ).start(),
        "mission": lambda: MissionAgent(
            kwargs.get("mission", "") or kwargs.get("objective", "") or kwargs.get("topic", ""),
            context=kwargs.get("context", ""),
            max_workers=kwargs.get("max_workers", 3),
            conversation_id=conversation_id,
            conversation_name=conversation_name,
            kind=kwargs.get("kind"),
        ).start(),
        # Timed no-LLM job for testing inbox / notify / clarifications.
        "demo": lambda: DemoAgent(
            duration=kwargs.get("duration", 20),
            topic=kwargs.get("topic")
            or kwargs.get("objective")
            or kwargs.get("message")
            or "demo work",
            ask=_as_bool(kwargs.get("ask"), False),
            question=kwargs.get("question", ""),
            require_user=_as_bool(kwargs.get("require_user"), True),
            default_if_timeout=kwargs.get("default_if_timeout", "default-choice"),
            conversation_id=conversation_id,
            conversation_name=conversation_name,
            kind=kwargs.get("kind"),
            message=kwargs.get("message", ""),
        ).start(),
        # OpenCode coding agent (user's installed CLI + default model)
        "opencode": lambda: OpenCodeAgent(
            objective=kwargs.get("objective") or "",
            prompt=kwargs.get("prompt") or "",
            topic=kwargs.get("topic") or "",
            command=kwargs.get("command") or "",
            message=kwargs.get("message") or "",
            directory=kwargs.get("directory")
            or kwargs.get("dir")
            or kwargs.get("cwd")
            or "",
            session_id=kwargs.get("session_id") or kwargs.get("session") or "",
            title=kwargs.get("title") or kwargs.get("description") or "",
            description=kwargs.get("description") or kwargs.get("title") or "",
            permissions=kwargs.get("permissions")
            or kwargs.get("permission_mode")
            or "auto",
            continue_session=_as_bool(
                kwargs.get("continue_session") or kwargs.get("continue"), False
            ),
            model=kwargs.get("model") or "",  # empty = OpenCode default
            context=kwargs.get("context") or "",
            conversation_id=conversation_id,
            conversation_name=conversation_name,
            kind=kwargs.get("kind"),
        ).start(),
        "coding": lambda: OpenCodeAgent(
            objective=kwargs.get("objective") or "",
            prompt=kwargs.get("prompt") or "",
            topic=kwargs.get("topic") or "",
            command=kwargs.get("command") or "",
            message=kwargs.get("message") or "",
            directory=kwargs.get("directory") or kwargs.get("dir") or "",
            session_id=kwargs.get("session_id") or kwargs.get("session") or "",
            title=kwargs.get("title") or kwargs.get("description") or "",
            description=kwargs.get("description") or kwargs.get("title") or "",
            permissions=kwargs.get("permissions") or "auto",
            continue_session=_as_bool(kwargs.get("continue_session"), False),
            model=kwargs.get("model") or "",
            context=kwargs.get("context") or "",
            conversation_id=conversation_id,
            conversation_name=conversation_name,
            kind=kwargs.get("kind"),
        ).start(),
    }

    if task_type not in agents:
        return "", f"Unknown task type: {task_type}"

    task_id = agents[task_type]()
    if not task_id:
        return "", "Failed to start background task."

    # Persist follow_up chain on the task so completion can auto-dispatch later.
    fu_specs = parse_follow_up_specs(follow_up_raw)
    registry = TaskRegistry()
    with registry._tasks_lock:
        live = registry._tasks.get(task_id)
        if live is not None:
            if fu_specs:
                # Store as list for multi-step, or single dict
                live.metadata["follow_up"] = (
                    fu_specs[0] if len(fu_specs) == 1 else fu_specs
                )
            if follow_up_depth is not None:
                try:
                    live.metadata["follow_up_depth"] = int(follow_up_depth)
                except (TypeError, ValueError):
                    live.metadata["follow_up_depth"] = 0
            if follow_up_parent:
                live.metadata["follow_up_parent_id"] = str(follow_up_parent)
            registry._persist_locked()

    ack = (
        f"I've started that task in the background (ID: {task_id}). "
        "I'll let you know when it's done."
    )
    if fu_specs:
        nxt = fu_specs[0]
        nxt_type = nxt.get("task_type") or nxt.get("type") or "task"
        nxt_topic = nxt.get("topic") or nxt.get("objective") or nxt.get("mission") or ""
        more = f" Then auto-start {nxt_type}"
        if nxt_topic:
            more += f" ({nxt_topic})"
        if len(fu_specs) > 1:
            more += f" and {len(fu_specs) - 1} more step(s)"
        ack += more + " when this finishes (works across new conversations)."
    return task_id, ack


def check_and_format_completions() -> Optional[str]:
    completion_queue = CompletionQueue()
    completed_tasks = completion_queue.pop_all()
    if not completed_tasks:
        return None
    summaries = []
    for task in completed_tasks:
        summaries.append(task.to_summary())
    return "\n".join(summaries)


def consume_and_store_pending_completions() -> None:
    completion_queue = CompletionQueue()
    completed_summary = check_and_format_completions()
    if completed_summary:
        completion_queue.store_pending(completed_summary)


def get_pending_completions_summary() -> str:
    """Unread long-job inbox for system-prompt injection (non-destructive).

    Short jobs (timers, etc.) are intentionally excluded — they already
    one-shot notified and must not reappear every conversation start.
    """
    try:
        from core.jobs.inbox import get_inbox

        return get_inbox().prompt_text()
    except Exception as e:
        print(f"[TaskManager] Inbox prompt failed: {e}")
        return ""


def get_pending_clarifications_summary() -> str:
    """Open clarification requests for system-prompt injection (non-destructive)."""
    try:
        from core.jobs.clarify import get_clarification_bridge

        return get_clarification_bridge().prompt_text()
    except Exception as e:
        print(f"[TaskManager] Clarify prompt failed: {e}")
        return ""


def sync_background_task_flag() -> None:
    """Derive background_task_active from live running / needs_input jobs."""
    from core.AppStates import background_task_active

    if TaskRegistry().get_running_tasks():
        background_task_active.set()
    else:
        background_task_active.clear()


def _task_is_handled(task: "BackgroundTask") -> bool:
    meta = task.metadata or {}
    if meta.get("handled") or meta.get("dismissed"):
        return True
    return False


def _task_age_label(task: "BackgroundTask") -> str:
    ts = task.completed_at or task.created_at
    if not ts:
        return "unknown age"
    secs = max(0, int((datetime.now() - ts).total_seconds()))
    if secs < 60:
        return f"{secs}s ago"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


def get_running_tasks_summary() -> str:
    """Status for the main agent: CURRENT work vs old handled noise."""
    registry = TaskRegistry()
    running = registry.get_running_tasks()
    lines: List[str] = []

    if running:
        lines.append("CURRENT — running:")
        for task in running:
            elapsed = (datetime.now() - task.created_at).seconds
            extra = ""
            if task.status == TaskStatus.NEEDS_INPUT and task.pending_request_id:
                extra = f", needs_input request={task.pending_request_id}"
            lines.append(
                f"  - {task.id}: {task.name} [{task.status.value}] "
                f"(running for {elapsed}s, started in conversation "
                f"'{task.conversation_name}'{extra})"
            )
    else:
        lines.append("CURRENT — no background tasks running.")

    # Needs attention: completed but not yet spoken/handled
    pending_notify = [
        t
        for t in registry.get_pending_notifications()
        if not _task_is_handled(t)
    ]
    if pending_notify:
        lines.append(
            "NEEDS ANNOUNCE (just finished, not yet fully handled — speak once then dismiss):"
        )
        for task in pending_notify[:5]:
            lines.append(
                f"  - {task.id}: {task.name} [{task.status.value}] "
                f"type={task.task_type} ({_task_age_label(task)})"
            )

    try:
        from core.jobs.inbox import get_inbox

        unread = get_inbox().list_unread() if hasattr(get_inbox(), "list_unread") else []
        if not unread:
            n = get_inbox().count_unread()
            if n:
                lines.append(
                    f"UNREAD INBOX: {n} long-job result(s) — "
                    "use manage_background_tasks result/dismiss with the id. "
                    "Do NOT re-announce unless user asks."
                )
        else:
            lines.append(
                "UNREAD INBOX (long jobs — only mention if user asks or DONE just fired):"
            )
            for item in unread[:5]:
                lines.append(
                    f"  - {item.id}: {item.title} — {(item.summary or '')[:80]}"
                )
    except Exception:
        pass

    try:
        from core.jobs.clarify import get_clarification_bridge

        pending = get_clarification_bridge().list_pending()
        if pending:
            lines.append("CURRENT — waiting on clarification:")
            for req in pending[:3]:
                lines.append(
                    f"  - job {req.job_id} request {req.id}: {req.question[:100]}"
                )
    except Exception:
        pass

    # Handled / old — brief, explicit DO NOT re-mention
    handled = [
        t
        for t in registry.get_all_tasks()
        if t.status
        in (
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.NOTIFIED,
        )
        and (_task_is_handled(t) or t.notified_at is not None)
    ]
    handled.sort(
        key=lambda t: (t.completed_at or t.created_at or datetime.min),
        reverse=True,
    )
    # Only show a couple of very recent handled items as "already done"
    recent_handled = []
    for t in handled[:8]:
        ts = t.completed_at or t.created_at
        if ts and (datetime.now() - ts).total_seconds() < 3600:
            recent_handled.append(t)
    if recent_handled:
        lines.append(
            "ALREADY HANDLED (do NOT re-mention, re-send, or re-announce unless user asks):"
        )
        for task in recent_handled[:4]:
            lines.append(
                f"  - {task.id}: {task.name} ({_task_age_label(task)}) — done"
            )

    # Held dossiers: finished work still usable ("run the program")
    try:
        from core.jobs.dossier import recent_completed_prompt

        dossier_block = recent_completed_prompt(max_items=5)
        if dossier_block and "(none" not in dossier_block.lower():
            lines.append(dossier_block)
    except Exception:
        pass

    return "\n".join(lines)


def collect_done_task_ids_from_history(limit_msgs: int = 20) -> List[str]:
    """Parse recent system DONE injects for task ids (for auto-ack after a turn)."""
    import re

    try:
        from core.messageHandler import messages, messages_lock
    except Exception:
        return []
    ids: List[str] = []
    with messages_lock:
        slice_ = list(messages)[-limit_msgs:]
    for msg in slice_:
        if msg.get("role") != "system":
            continue
        content = str(msg.get("content") or "")
        if "BACKGROUND TASK DONE" not in content:
            continue
        if "BACKGROUND TASK HANDLED" in content:
            continue
        for m in re.finditer(r"\bid=([A-Za-z0-9_\-]+)", content):
            tid = m.group(1)
            if tid not in ids:
                ids.append(tid)
    return ids


def list_open_main_actions() -> List[Dict[str, str]]:
    """
    Completed jobs that still need the MAIN agent to run a small action
    (WhatsApp, weather+message, etc.). Not yet handled.
    """
    registry = TaskRegistry()
    out: List[Dict[str, str]] = []
    for task in registry.get_all_tasks():
        if task.status not in (TaskStatus.COMPLETED, TaskStatus.FAILED):
            continue
        if _task_is_handled(task):
            continue
        action = str((task.metadata or {}).get("follow_up_main_action") or "").strip()
        if not action:
            continue
        out.append(
            {
                "task_id": task.id,
                "action": action,
                "name": task.name or task.task_type,
            }
        )
    # Newest first
    out.reverse()
    return out


def inject_open_main_actions(*, trigger_response: bool = False) -> int:
    """
    Re-surface unfinished main_action work as a hard system instruction.
    Call at the start of every main turn so 'ok' after a failed DONE still works.
    """
    open_actions = list_open_main_actions()
    if not open_actions:
        return 0
    try:
        from core.messageHandler import add_message, messages, messages_lock
    except Exception:
        return 0

    # Avoid stacking identical nudges every turn
    with messages_lock:
        recent = list(messages)[-8:]
    already = any(
        m.get("role") == "system"
        and "[PENDING MAIN ACTION]" in str(m.get("content") or "")
        for m in recent
    )
    if already and not trigger_response:
        # Still count as present
        return len(open_actions)

    lines = [
        "[PENDING MAIN ACTION — DO THIS NOW]",
        "A finished timer/job left work for YOU (main agent). "
        "Call tools immediately (web weather, get_time_date, whatsapp_plugin, etc.). "
        "Do NOT only say 'got it' or 'what's next'. Do NOT spawn delegate.",
    ]
    for item in open_actions[:3]:
        lines.append(f"- task_id={item['task_id']} ({item['name']}): {item['action']}")
    lines.append(
        "After tools succeed, confirm in one short sentence. "
        "Then the system will dismiss these tasks."
    )
    try:
        add_message(
            role="system",
            content="\n".join(lines),
            tool_id="",
            trigger_response=bool(trigger_response),
        )
        print(
            f"[TaskManager] Injected {len(open_actions)} open main_action(s)"
        )
        return len(open_actions)
    except Exception as e:
        print(f"[TaskManager] inject_open_main_actions failed: {e}")
        return 0


def auto_acknowledge_after_turn(
    *,
    tools_called: bool = False,
    spoke: bool = False,
) -> List[str]:
    """
    After a main-agent turn that processed DONE injects, mark jobs handled.

    CRITICAL: if the job has follow_up_main_action still open, only ack when
    tools_called=True (weather/WhatsApp/etc. actually ran). Pure speech like
    "Background task update received" or "Got it" must NOT dismiss the action.

    Long jobs (research/opencode) that were *spoken* this turn are also fully
    acknowledged + DONE injects redacted — otherwise the same completion can
    wake the main agent 3–4 times with near-identical replies.
    """
    if not tools_called and not spoke:
        return []
    registry = TaskRegistry()
    acked: List[str] = []
    open_ids = {x["task_id"] for x in list_open_main_actions()}

    # Prefer ids from history; also any open main_action parents
    candidate_ids = list(collect_done_task_ids_from_history())
    for tid in open_ids:
        if tid not in candidate_ids:
            candidate_ids.append(tid)

    for tid in candidate_ids:
        task = registry.get_task(tid)
        if not task:
            continue
        if _task_is_handled(task):
            continue
        has_main_action = bool(
            str((task.metadata or {}).get("follow_up_main_action") or "").strip()
        )
        if has_main_action and not tools_called:
            # Keep pending — inject will re-surface next turn
            print(
                f"[TaskManager] Not acking {tid}: main_action still needs tools"
            )
            continue
        # Short, timers, main_action, OR any long job we just spoke about
        # (opencode/research) — redact DONE so it cannot re-fire this session.
        if (
            task.kind == "short"
            or task.task_type == "timer"
            or has_main_action
            or spoke
            or task.task_type in ("opencode", "coding", "research", "delegate", "mission", "demo")
        ):
            if registry.acknowledge_completion(
                tid,
                reason=(
                    "auto_after_tools"
                    if tools_called
                    else "auto_after_announce_turn"
                ),
                redact_history=True,
            ):
                acked.append(tid)
                try:
                    from core.jobs.announce_queue import get_announce_queue

                    get_announce_queue().mark_announced(tid)
                except Exception:
                    pass
        else:
            if task.notified_at is None:
                registry.mark_as_notified(tid)
    return acked



def manage_background_tasks(
    action: str = "list",
    task_id: str = "",
    answer: str = "",
    request_id: str = "",
    source: str = "user",
) -> str:
    registry = TaskRegistry()
    action = (action or "list").lower().strip()
    task_id = (task_id or "").strip()
    request_id = (request_id or "").strip()
    answer = answer if answer is not None else ""
    source = (source or "user").strip().lower()
    if source not in ("main", "user"):
        source = "user"

    if action == "list":
        tasks = registry.get_all_tasks()
        if not tasks:
            return "No background tasks are known right now."
        tasks = sorted(
            tasks,
            key=lambda t: (t.completed_at or t.created_at or datetime.min),
            reverse=True,
        )
        current: List[str] = []
        needs: List[str] = []
        old: List[str] = []
        for task in tasks[:40]:
            handled = _task_is_handled(task)
            age = _task_age_label(task)
            base = (
                f"- {task.id}: {task.name} [{task.status.value}] "
                f"type={task.task_type} kind={task.kind} ({age})"
            )
            if task.status in (
                TaskStatus.RUNNING,
                TaskStatus.PENDING,
                TaskStatus.NEEDS_INPUT,
            ):
                if task.status == TaskStatus.NEEDS_INPUT:
                    try:
                        from core.jobs.clarify import get_clarification_bridge

                        req = get_clarification_bridge().get_pending_for_job(task.id)
                        if req:
                            base += f" | Q: {req.question[:80]}"
                    except Exception:
                        pass
                current.append(base)
            elif task.notified_at is None and not handled:
                needs.append(base + " | needs announce/handle")
            elif handled or task.notified_at is not None:
                old.append(base + " | HANDLED — do not re-mention")
            else:
                old.append(base)
        lines = ["Background tasks (current vs old):"]
        lines.append("CURRENT / ACTIVE:")
        lines.extend(current[:15] or ["  (none)"])
        if needs:
            lines.append("NEEDS ATTENTION (finish once, then dismiss):")
            lines.extend(needs[:10])
        lines.append("OLD / HANDLED (ignore unless user asks):")
        lines.extend(old[:10] or ["  (none)"])
        return "\n".join(lines)

    if action in {"opencode_list", "opencode_sessions"}:
        try:
            from functions.system.opencode_client import (
                get_session_store,
                list_native_sessions,
                sessions_prompt_block,
            )

            q = (task_id or answer or "").strip()
            store = get_session_store()
            if q:
                rows = store.find_by_description(q, limit=15)
            else:
                rows = store.list_recent(15)
            lines = ["OpenCode sessions (AVA index — use session_id to continue):"]
            if rows:
                for s in rows:
                    lines.append(
                        f"- {s.session_id}: {s.description or s.title or '(no desc)'} "
                        f"[{s.status}] dir={s.directory or '?'} job={s.job_id or '-'}"
                    )
            else:
                lines.append("  (AVA index empty — showing native OpenCode list)")
                for s in list_native_sessions(12):
                    lines.append(
                        f"- {s.get('id')}: {s.get('title') or '(untitled)'} "
                        f"dir={s.get('directory') or '?'}"
                    )
            lines.append("")
            lines.append(
                "Continue: background_task task_type=opencode session_id=<id> "
                "objective=<next instruction> directory=<same dir> permissions=auto|ask|deny"
            )
            lines.append(sessions_prompt_block(5))
            return "\n".join(lines)
        except Exception as e:
            return f"opencode_list failed: {e}"

    if action in {"context", "dossier"}:
        if not task_id:
            # List recent held dossiers if no id
            try:
                from core.jobs.dossier import recent_completed_prompt

                return recent_completed_prompt(max_items=12)
            except Exception as e:
                return f"Could not list dossiers: {e}"
        try:
            from core.jobs.dossier import format_task_context

            return format_task_context(task_id, full=True)
        except Exception as e:
            return f"Could not load dossier for {task_id}: {e}"

    if action in {"status", "result"}:
        if not task_id:
            return "Please provide task_id."
        task = registry.get_task(task_id)
        if not task:
            return f"Task {task_id} was not found."
        if action == "result":
            # Reading a result acknowledges the unread inbox item (long jobs).
            try:
                from core.jobs.inbox import get_inbox

                get_inbox().acknowledge(task_id)
            except Exception as e:
                print(f"[TaskManager] Inbox ack failed: {e}")
        if action == "status":
            payload = task.to_dict()
            if task.status == TaskStatus.NEEDS_INPUT:
                try:
                    from core.jobs.clarify import get_clarification_bridge

                    req = get_clarification_bridge().get_pending_for_job(task.id)
                    if req:
                        payload["pending_question"] = req.question
                        payload["pending_options"] = req.options
                except Exception:
                    pass
            # Attach held dossier brief so main sees continue hints without full dump
            try:
                from core.jobs.dossier import get_dossier_for_task

                d = get_dossier_for_task(task)
                if d:
                    payload["dossier"] = {
                        "origin_user_request": d.origin_user_request,
                        "mission": d.mission,
                        "result_summary": d.result_summary,
                        "artifacts": d.artifacts,
                        "continue_hints": d.continue_hints,
                        "announce_policy": d.announce_policy,
                        "held": d.held,
                    }
            except Exception:
                pass
            return json.dumps(payload, indent=2)
        # result action (and fallthrough for finished states)
        if task.status == TaskStatus.FAILED:
            return f"Task failed: {task.error}"
        if task.status == TaskStatus.NEEDS_INPUT:
            try:
                from core.jobs.clarify import get_clarification_bridge

                req = get_clarification_bridge().get_pending_for_job(task.id)
                if req:
                    return (
                        f"Task {task.id} is waiting for input: {req.question} "
                        f"(request_id={req.id}). Use action answer to resume."
                    )
            except Exception:
                pass
            return f"Task {task.id} is waiting for input."
        # COMPLETED, NOTIFIED (legacy), or any row that already has a result body
        result_body = ""
        if task.result is not None and str(task.result).strip() != "":
            result_body = str(task.result)
        elif task.status in (TaskStatus.COMPLETED, TaskStatus.NOTIFIED):
            result_body = "Task completed with no result."
        elif task.status == TaskStatus.CANCELLED:
            result_body = f"Task {task.id} was cancelled."
        else:
            result_body = ""
        # Append held dossier so "run the program" has paths/hints next to result
        try:
            from core.jobs.dossier import format_task_context

            dossier_txt = format_task_context(task_id, full=False)
            if dossier_txt and "No dossier" not in dossier_txt:
                if result_body:
                    return result_body + "\n\n" + dossier_txt
                return dossier_txt
        except Exception:
            pass
        if result_body:
            return result_body
        if task.status == TaskStatus.CANCELLED:
            return f"Task {task.id} was cancelled."
        return (
            f"Task {task.id} is {task.status.value}; "
            f"no final result is available yet."
        )

    if action == "cancel":
        if not task_id:
            return "Please provide task_id."
        if registry.cancel_task(task_id):
            return f"Cancellation requested for task {task_id}."
        return f"Task {task_id} could not be cancelled. It may be finished or unknown."

    if action == "dismiss":
        # Dismiss one task, or all handled/short completions when task_id is
        # "all" / "handled" / "short".
        raw = (task_id or "").strip().lower()
        if raw in ("all", "handled", "short", "old", "*"):
            n = 0
            for t in list(registry.get_all_tasks()):
                if t.status not in (
                    TaskStatus.COMPLETED,
                    TaskStatus.FAILED,
                    TaskStatus.CANCELLED,
                    TaskStatus.NOTIFIED,
                ):
                    continue
                if raw in ("short",) and not (
                    t.kind == "short" or t.task_type == "timer"
                ):
                    continue
                if registry.acknowledge_completion(
                    t.id, reason="dismiss_bulk", redact_history=True
                ):
                    n += 1
                try:
                    from core.jobs.inbox import get_inbox

                    get_inbox().dismiss(t.id)
                except Exception:
                    pass
            return f"Dismissed/acknowledged {n} finished task(s)."

        if not task_id:
            return (
                "Provide task_id to dismiss, or task_id='short'/'all' to clear "
                "finished noise."
            )
        ok = registry.acknowledge_completion(
            task_id, reason="dismiss", redact_history=True
        )
        inbox_ok = False
        try:
            from core.jobs.inbox import get_inbox

            inbox_ok = bool(get_inbox().dismiss(task_id))
        except Exception as e:
            print(f"[TaskManager] Inbox dismiss failed: {e}")
        if ok or inbox_ok:
            return (
                f"Dismissed task {task_id} "
                f"(registry={ok}, inbox={inbox_ok}). Do not re-mention it."
            )
        return f"Task {task_id} not found (nothing to dismiss)."

    if action == "answer":
        if not (answer or "").strip():
            return "Please provide answer text."
        if not request_id and not task_id:
            return "Please provide request_id or task_id for answer."
        try:
            from core.jobs.clarify import get_clarification_bridge

            return get_clarification_bridge().answer(
                answer=answer,
                request_id=request_id,
                task_id=task_id,
                source=source,
            )
        except Exception as e:
            return f"Failed to deliver answer: {e}"

    return "Unknown action. Use list, status, result, cancel, dismiss, or answer."


def get_task_status(task_id: str) -> Optional[Dict[str, Any]]:
    registry = TaskRegistry()
    task = registry.get_task(task_id)
    if task:
        return task.to_dict()
    return None


def list_all_tasks() -> List[Dict[str, Any]]:
    registry = TaskRegistry()
    tasks = registry.get_all_tasks()
    return [t.to_dict() for t in tasks]


def update_conversation_state(conversation_id: str, conversation_name: str = "",
                              is_active: bool = True):
    state_manager = ConversationStateManager()
    if is_active:
        state_manager.conversation_started(conversation_id, conversation_name)
    else:
        state_manager.conversation_ended()


def set_speaking_state(is_speaking: bool):
    state_manager = ConversationStateManager()
    state_manager.is_speaking = is_speaking


def set_listening_state(is_listening: bool):
    state_manager = ConversationStateManager()
    state_manager.is_listening = is_listening


def set_mode(mode: str):
    state_manager = ConversationStateManager()
    state_manager.current_mode = mode


def user_activity_occurred():
    state_manager = ConversationStateManager()
    state_manager.user_spoke()


def deliver_notification(task: BackgroundTask):
    engine = NotificationDecisionEngine()
    return engine.decide_notification_strategy(task)


def process_completed_task(task: BackgroundTask):
    completion_queue = CompletionQueue()
    completion_queue.push(task)


# Backward compatibility - these functions maintain the original API
_get_task_registry = None
_get_completion_queue = None


def get_registry() -> TaskRegistry:
    global _get_task_registry
    if _get_task_registry is None:
        _get_task_registry = TaskRegistry()
    return _get_task_registry


def get_completion_queue() -> CompletionQueue:
    global _get_completion_queue
    if _get_completion_queue is None:
        _get_completion_queue = CompletionQueue()
    return _get_completion_queue


# For backward compatibility with existing imports
CompletionQueue = CompletionQueue
TaskRegistry = TaskRegistry
