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

import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Callable, Dict, List, Optional
from collections import defaultdict


# ============================================================================
# ENUMS
# ============================================================================

class TaskStatus(Enum):
    """Status of a background task"""
    PENDING = "pending"
    RUNNING = "running"
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
    priority: TaskPriority = TaskPriority.NORMAL
    notification_preference: NotificationPreference = NotificationPreference.NEXT_TURN
    created_at: datetime = field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None
    notified_at: Optional[datetime] = None
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
            "priority": self.priority.value,
            "created_at": self.created_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "notified_at": self.notified_at.isoformat() if self.notified_at else None,
            "notification_preference": self.notification_preference.value,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BackgroundTask":
        return cls(
            id=data.get("id", ""),
            name=data.get("name", "Unknown"),
            description=data.get("description", ""),
            status=TaskStatus(data.get("status", "pending")),
            result=data.get("result"),
            error=data.get("error"),
            conversation_id=data.get("conversation_id", ""),
            conversation_name=data.get("conversation_name", ""),
            original_query=data.get("original_query", ""),
            task_type=data.get("task_type", "general"),
            priority=TaskPriority(data.get("priority", "normal")),
            created_at=datetime.fromisoformat(data.get("created_at", datetime.now().isoformat())),
            completed_at=datetime.fromisoformat(data.get("completed_at")) if data.get("completed_at") else None,
            notified_at=datetime.fromisoformat(data.get("notified_at")) if data.get("notified_at") else None,
            notification_preference=NotificationPreference(data.get("notification_preference", "next_turn")),
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
        self._tasks_lock = threading.Lock()
        self._initialized = True

    def create_task(self, name: str, description: str,
                   conversation_id: str = "", conversation_name: str = "",
                   original_query: str = "", task_type: str = "general",
                   priority: TaskPriority = TaskPriority.NORMAL) -> BackgroundTask:
        task_id = str(uuid.uuid4())[:8]
        task = BackgroundTask(
            id=task_id,
            name=name,
            description=description,
            conversation_id=conversation_id,
            conversation_name=conversation_name,
            original_query=original_query,
            task_type=task_type,
            priority=priority,
        )
        with self._tasks_lock:
            self._tasks[task_id] = task
            if conversation_id:
                self._conversation_tasks[conversation_id].append(task_id)
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

    def get_task(self, task_id: str) -> Optional[BackgroundTask]:
        with self._tasks_lock:
            return self._tasks.get(task_id)

    def get_running_tasks(self) -> List[BackgroundTask]:
        with self._tasks_lock:
            return [t for t in self._tasks.values()
                    if t.status == TaskStatus.RUNNING]

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
        with self._tasks_lock:
            if task_id in self._tasks:
                self._tasks[task_id].notified_at = datetime.now()
                self._tasks[task_id].status = TaskStatus.NOTIFIED

    def cancel_task(self, task_id: str) -> bool:
        with self._tasks_lock:
            if task_id in self._tasks:
                task = self._tasks[task_id]
                if task.status == TaskStatus.RUNNING:
                    task.status = TaskStatus.CANCELLED
                    task.completed_at = datetime.now()
                    return True
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

    def clear_all(self):
        with self._tasks_lock:
            self._tasks.clear()
            self._conversation_tasks.clear()


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
        current_conv_id = self.state_manager.current_conversation_id
        is_active = self.state_manager.is_conversation_active
        current_mode = self.state_manager.current_mode

        # Case 1: Original conversation still active
        if current_conv_id == task.conversation_id and is_active:
            return NotificationStrategy(
                delivery_method=DeliveryMethod.NEXT_RESPONSE,
                notification_text=task.to_conversation_context(),
                interrupt_current=False,
                priority=task.priority,
                task_id=task.id,
            )

        # Case 2: No conversation active (wake-word mode, idle)
        if not is_active or current_mode in ["wakeword", "idle"]:
            if task.priority in [TaskPriority.HIGH, TaskPriority.URGENT]:
                return NotificationStrategy(
                    delivery_method=DeliveryMethod.IMMEDIATE,
                    notification_text=task.to_idle_notification(),
                    interrupt_current=True,
                    priority=task.priority,
                    task_id=task.id,
                )
            else:
                return NotificationStrategy(
                    delivery_method=DeliveryMethod.QUEUE_FOR_IDLE,
                    notification_text=task.to_idle_notification(),
                    interrupt_current=False,
                    priority=task.priority,
                    task_id=task.id,
                )

        # Case 3: Different conversation active
        if current_conv_id and current_conv_id != task.conversation_id:
            current_conv_relevance = self._assess_conversation_relevance(current_conv_id, task)

            if task.priority == TaskPriority.URGENT:
                return NotificationStrategy(
                    delivery_method=DeliveryMethod.IMMEDIATE,
                    notification_text=task.to_different_conversation_notification(),
                    interrupt_current=True,
                    priority=task.priority,
                    task_id=task.id,
                )
            elif current_conv_relevance > 0.7:
                return NotificationStrategy(
                    delivery_method=DeliveryMethod.NEXT_RESPONSE,
                    notification_text=task.to_different_conversation_notification(),
                    interrupt_current=False,
                    priority=task.priority,
                    task_id=task.id,
                )
            else:
                return NotificationStrategy(
                    delivery_method=DeliveryMethod.DEFER,
                    notification_text="",
                    interrupt_current=False,
                    priority=task.priority,
                    task_id=task.id,
                )

        return NotificationStrategy(
            delivery_method=DeliveryMethod.DEFER,
            notification_text="",
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
                 priority: TaskPriority = TaskPriority.NORMAL):
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
            priority=priority
        )
        self._thread: Optional[threading.Thread] = None

    def execute(self, **kwargs) -> Any:
        raise NotImplementedError("Subclasses must implement execute()")

    def _run(self, **kwargs):
        try:
            self.registry.update_task(self.task.id, TaskStatus.RUNNING)
            result = self.execute(**kwargs)
            self.registry.update_task(
                self.task.id,
                TaskStatus.COMPLETED,
                result=result
            )
            completed_task = self.registry.get_task(self.task.id)
            if completed_task:
                self.completion_queue.push(completed_task)
        except Exception as e:
            self.registry.update_task(
                self.task.id,
                TaskStatus.FAILED,
                error=str(e)
            )
            completed_task = self.registry.get_task(self.task.id)
            if completed_task:
                self.completion_queue.push(completed_task)
        finally:
            self._thread = None

    def start(self, **kwargs) -> str:
        self._thread = threading.Thread(
            target=self._run,
            kwargs=kwargs,
            daemon=True
        )
        self._thread.start()
        return self.task.id

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()


# ============================================================================
# CONCRETE SUB-AGENTS
# ============================================================================

class ResearchAgent(SubAgent):
    def __init__(self, topic: str, conversation_id: str = "",
                 conversation_name: str = "", priority: TaskPriority = TaskPriority.NORMAL):
        super().__init__(
            task_name=f"Research: {topic}",
            task_description=f"Researching information about {topic}",
            conversation_id=conversation_id,
            conversation_name=conversation_name,
            original_query=topic,
            task_type="research",
            priority=priority
        )
        self.topic = topic

    def execute(self, **kwargs) -> str:
        from functions.web.deep_research import research
        result = research(self.topic)
        return result


class WebScrapingAgent(SubAgent):
    def __init__(self, url: str, purpose: str = "data extraction",
                 conversation_id: str = "", conversation_name: str = ""):
        super().__init__(
            task_name=f"Web Scraping",
            task_description=f"Fetching data from {url} for {purpose}",
            conversation_id=conversation_id,
            conversation_name=conversation_name,
            original_query=f"Scrape {url} for {purpose}",
            task_type="web_scrape",
        )
        self.url = url

    def execute(self, **kwargs) -> str:
        from functions.web.internet import fetch_website_data
        result = fetch_website_data(url=self.url)
        return result


class TimerAgent(SubAgent):
    def __init__(self, duration_seconds: int, message: str = "Timer complete",
                 conversation_id: str = "", conversation_name: str = ""):
        super().__init__(
            task_name=f"Timer ({duration_seconds}s)",
            task_description=message,
            conversation_id=conversation_id,
            conversation_name=conversation_name,
            original_query=message,
            task_type="timer",
        )
        self.duration = duration_seconds
        self.message = message

    def execute(self, **kwargs) -> str:
        time.sleep(self.duration)
        return self.message


class SmartHomeAgent(SubAgent):
    def __init__(self, command: str, conversation_id: str = "",
                 conversation_name: str = ""):
        super().__init__(
            task_name=f"SmartHome: {command[:20]}...",
            task_description=f"Executing smart home command: {command}",
            conversation_id=conversation_id,
            conversation_name=conversation_name,
            original_query=command,
            task_type="smarthome",
        )
        self.command = command

    def execute(self, **kwargs) -> str:
        from functions.system.smart_home_agent import run_smarthome_agent
        result = run_smarthome_agent(self.command)
        return result


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

    agents = {
        "research": lambda: ResearchAgent(
            kwargs.get("topic", ""),
            conversation_id=conversation_id,
            conversation_name=conversation_name
        ).start(),
        "scrape": lambda: WebScrapingAgent(
            kwargs.get("url", ""),
            kwargs.get("purpose", ""),
            conversation_id=conversation_id,
            conversation_name=conversation_name
        ).start(),
        "timer": lambda: TimerAgent(
            kwargs.get("duration", 60),
            kwargs.get("message", "Timer complete"),
            conversation_id=conversation_id,
            conversation_name=conversation_name
        ).start(),
        "smarthome": lambda: SmartHomeAgent(
            kwargs.get("command", ""),
            conversation_id=conversation_id,
            conversation_name=conversation_name
        ).start(),
    }

    if task_type not in agents:
        return "", f"Unknown task type: {task_type}"

    task_id = agents[task_type]()
    return task_id, f"I've started that task in the background (ID: {task_id}). I'll let you know when it's done."


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
    completion_queue = CompletionQueue()
    items = completion_queue.get_and_clear_pending()
    if not items:
        return ""
    return "\n".join(items)


def get_running_tasks_summary() -> str:
    registry = TaskRegistry()
    running = registry.get_running_tasks()
    if not running:
        return "No background tasks are currently running."
    lines = ["Currently running background tasks:"]
    for task in running:
        elapsed = (datetime.now() - task.created_at).seconds
        lines.append(f"  - {task.name} (running for {elapsed}s, started in conversation '{task.conversation_name}')")
    return "\n".join(lines)


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
