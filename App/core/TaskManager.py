"""
TaskManager.py - Manages background tasks and sub-agents for AVA

This module provides:
1. TaskRegistry - Track all running background tasks
2. CompletionQueue - Store completed task results for notification
3. SubAgent - Base class for background workers
4. Notification system that respects main agent state
"""

import threading
import queue
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, Any, Optional, Dict, List
from enum import Enum
from datetime import datetime


class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Task:
    """Represents a background task"""
    id: str
    name: str
    description: str
    status: TaskStatus = TaskStatus.PENDING
    result: Any = None
    error: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None
    
    def to_summary(self) -> str:
        """Generate a human-readable summary for the LLM"""
        if self.status == TaskStatus.COMPLETED:
            return f"[BACKGROUND TASK COMPLETED] Task '{self.name}': {self.result}"
        elif self.status == TaskStatus.FAILED:
            return f"[BACKGROUND TASK FAILED] Task '{self.name}': {self.error}"
        return f"[TASK IN PROGRESS] Task '{self.name}'"


class TaskRegistry:
    """
    Central registry for all background tasks.
    Thread-safe singleton that tracks task lifecycle.
    """
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
        self._tasks: Dict[str, Task] = {}
        self._tasks_lock = threading.Lock()
        self._initialized = True
    
    def create_task(self, name: str, description: str) -> Task:
        """Create and register a new task"""
        task_id = str(uuid.uuid4())[:8]
        task = Task(id=task_id, name=name, description=description)
        with self._tasks_lock:
            self._tasks[task_id] = task
        return task
    
    def update_task(self, task_id: str, status: TaskStatus, 
                    result: Any = None, error: str = None):
        """Update task status and optionally result/error"""
        with self._tasks_lock:
            if task_id in self._tasks:
                task = self._tasks[task_id]
                task.status = status
                if result is not None:
                    task.result = result
                if error is not None:
                    task.error = error
                if status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
                    task.completed_at = datetime.now()
    
    def get_task(self, task_id: str) -> Optional[Task]:
        """Get a task by ID"""
        with self._tasks_lock:
            return self._tasks.get(task_id)
    
    def get_running_tasks(self) -> List[Task]:
        """Get all currently running tasks"""
        with self._tasks_lock:
            return [t for t in self._tasks.values() 
                    if t.status == TaskStatus.RUNNING]
    
    def get_pending_notifications(self) -> List[Task]:
        """Get completed/failed tasks that haven't been announced yet"""
        with self._tasks_lock:
            completed = [t for t in self._tasks.values() 
                        if t.status in (TaskStatus.COMPLETED, TaskStatus.FAILED)]
            # Remove from registry after fetching
            for task in completed:
                del self._tasks[task.id]
            return completed


class CompletionQueue:
    """
    Thread-safe queue for completed task notifications.
    The main agent checks this queue to announce completions.
    """
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
        self._queue = queue.Queue()
        self._initialized = True
    
    def push(self, task: Task):
        """Add a completed task to the notification queue"""
        self._queue.put(task)
    
    def pop(self, timeout: float = 0.1) -> Optional[Task]:
        """Get next completed task, or None if queue is empty"""
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None
    
    def pop_all(self) -> List[Task]:
        """Get all completed tasks from the queue"""
        tasks = []
        while True:
            try:
                tasks.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return tasks
    
    def has_notifications(self) -> bool:
        """Check if there are pending notifications"""
        return not self._queue.empty()


class SubAgent:
    """
    Base class for background sub-agents.
    
    Usage:
        class ResearchAgent(SubAgent):
            def execute(self, query: str) -> str:
                # Do research...
                return "Research results..."
        
        agent = ResearchAgent("Research quantum computing")
        agent.start(query="quantum computing basics")
    """
    
    def __init__(self, task_name: str, task_description: str = ""):
        self.registry = TaskRegistry()
        self.completion_queue = CompletionQueue()
        self.task = self.registry.create_task(task_name, task_description)
        self._thread: Optional[threading.Thread] = None
    
    def execute(self, **kwargs) -> Any:
        """
        Override this method with your task logic.
        Return the result, or raise an exception on failure.
        """
        raise NotImplementedError("Subclasses must implement execute()")
    
    def _run(self, **kwargs):
        """Internal runner that wraps execute() with status updates"""
        try:
            self.registry.update_task(self.task.id, TaskStatus.RUNNING)
            result = self.execute(**kwargs)
            self.registry.update_task(
                self.task.id, 
                TaskStatus.COMPLETED, 
                result=result
            )
            # Push to completion queue for notification
            self.completion_queue.push(self.registry.get_task(self.task.id))
        except Exception as e:
            self.registry.update_task(
                self.task.id, 
                TaskStatus.FAILED, 
                error=str(e)
            )
            self.completion_queue.push(self.registry.get_task(self.task.id))
    
    def start(self, **kwargs) -> str:
        """Start the sub-agent in a background thread. Returns task ID."""
        self._thread = threading.Thread(
            target=self._run, 
            kwargs=kwargs, 
            daemon=True
        )
        self._thread.start()
        return self.task.id
    
    def is_running(self) -> bool:
        """Check if the sub-agent is still running"""
        return self._thread is not None and self._thread.is_alive()


# ============================================================================
# Concrete Sub-Agent Examples
# ============================================================================

class ResearchAgent(SubAgent):
    """Sub-agent for web research tasks"""
    
    def __init__(self, topic: str):
        super().__init__(
            task_name=f"Research: {topic}",
            task_description=f"Researching information about {topic}"
        )
        self.topic = topic
    
    def execute(self, **kwargs) -> str:
        # Import here to avoid circular imports
        from functions.google_ai_response import get_google_ai_response
        
        # Perform the research
        result = get_google_ai_response(query=self.topic)
        return result


class WebScrapingAgent(SubAgent):
    """Sub-agent for fetching and processing website data"""
    
    def __init__(self, url: str, purpose: str = "data extraction"):
        super().__init__(
            task_name=f"Web Scraping",
            task_description=f"Fetching data from {url} for {purpose}"
        )
        self.url = url
    
    def execute(self, **kwargs) -> str:
        from functions.web_data import fetch_website_data
        result = fetch_website_data(url=self.url)
        return result


class TimerAgent(SubAgent):
    """Sub-agent for timer/reminder functionality"""
    
    def __init__(self, duration_seconds: int, message: str = "Timer complete"):
        super().__init__(
            task_name=f"Timer ({duration_seconds}s)",
            task_description=message
        )
        self.duration = duration_seconds
        self.message = message
    
    def execute(self, **kwargs) -> str:
        time.sleep(self.duration)
        return self.message


# ============================================================================
# Integration Helper Functions
# ============================================================================

def dispatch_background_task(task_type: str, **kwargs) -> tuple[str, str]:
    """
    Dispatch a background task based on type.
    Returns (task_id, acknowledgment_message)
    
    Usage in FuncHandler:
        task_id, ack = dispatch_background_task("research", topic="AI safety")
    """
    agents = {
        "research": lambda: ResearchAgent(kwargs.get("topic", "")).start(),
        "scrape": lambda: WebScrapingAgent(
            kwargs.get("url", ""), 
            kwargs.get("purpose", "")
        ).start(),
        "timer": lambda: TimerAgent(
            kwargs.get("duration", 60),
            kwargs.get("message", "Timer complete")
        ).start(),
    }
    
    if task_type not in agents:
        return "", f"Unknown task type: {task_type}"
    
    task_id = agents[task_type]()
    return task_id, f"I've started that task in the background (ID: {task_id}). I'll let you know when it's done."


def check_and_format_completions() -> Optional[str]:
    """
    Check for completed background tasks and format them for injection
    into the conversation.
    
    Returns a formatted string if there are completions, None otherwise.
    """
    completion_queue = CompletionQueue()
    completed_tasks = completion_queue.pop_all()
    
    if not completed_tasks:
        return None
    
    summaries = []
    for task in completed_tasks:
        summaries.append(task.to_summary())
    
    return "\n".join(summaries)


def get_running_tasks_summary() -> str:
    """Get a summary of all currently running background tasks"""
    registry = TaskRegistry()
    running = registry.get_running_tasks()
    
    if not running:
        return "No background tasks are currently running."
    
    lines = ["Currently running background tasks:"]
    for task in running:
        elapsed = (datetime.now() - task.created_at).seconds
        lines.append(f"  - {task.name} (running for {elapsed}s)")
    
    return "\n".join(lines)
