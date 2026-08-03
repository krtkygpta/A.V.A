"""
clarify.py - Sub-agent ↔ main agent clarification bridge.

Background workers call ``ask_main(...)`` when they hit user-specific or
ambiguous choices. They block (this worker only) until main answers itself,
the user answers via ``manage_background_tasks(answer=...)``, the request
times out, or the job is cancelled.

Open requests persist to JSON so conversation-start can re-inject them after
main-agent sleep. Worker threads that die mid-wait cannot resume after process
kill (v1 orphan behavior).
"""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

CLARIFY_STORE_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "data"
    / "tasks"
    / "clarifications.json"
)

MAX_CLARIFICATIONS_PER_JOB = 5
DEFAULT_TIMEOUT_LONG = 3600
DEFAULT_TIMEOUT_SHORT = 120


@dataclass
class ClarificationRequest:
    id: str
    job_id: str
    question: str
    options: Optional[List[str]] = None
    context_snippet: str = ""
    default_if_timeout: Optional[str] = None
    allow_main_self_answer: bool = True
    require_user: bool = False
    created_at: datetime = field(default_factory=datetime.now)
    timeout_seconds: int = DEFAULT_TIMEOUT_LONG
    status: str = "pending"  # pending | answered | timeout | cancelled
    answer: Optional[str] = None
    source: Optional[str] = None  # main | user | timeout | cancelled
    answered_at: Optional[datetime] = None
    job_name: str = ""
    woken: bool = False  # response_loop already delivered wake inject

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "job_id": self.job_id,
            "question": self.question,
            "options": self.options,
            "context_snippet": self.context_snippet,
            "default_if_timeout": self.default_if_timeout,
            "allow_main_self_answer": self.allow_main_self_answer,
            "require_user": self.require_user,
            "created_at": self.created_at.isoformat(),
            "timeout_seconds": self.timeout_seconds,
            "status": self.status,
            "answer": self.answer,
            "source": self.source,
            "answered_at": self.answered_at.isoformat() if self.answered_at else None,
            "job_name": self.job_name,
            "woken": self.woken,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ClarificationRequest":
        answered = data.get("answered_at")
        return cls(
            id=data.get("id", ""),
            job_id=data.get("job_id", ""),
            question=data.get("question", ""),
            options=data.get("options"),
            context_snippet=data.get("context_snippet", "") or "",
            default_if_timeout=data.get("default_if_timeout"),
            allow_main_self_answer=bool(data.get("allow_main_self_answer", True)),
            require_user=bool(data.get("require_user", False)),
            created_at=datetime.fromisoformat(
                data.get("created_at", datetime.now().isoformat())
            ),
            timeout_seconds=int(data.get("timeout_seconds", DEFAULT_TIMEOUT_LONG)),
            status=data.get("status", "pending"),
            answer=data.get("answer"),
            source=data.get("source"),
            answered_at=datetime.fromisoformat(answered) if answered else None,
            job_name=data.get("job_name", "") or "",
            woken=bool(data.get("woken", False)),
        )

    def to_compact_inject(self) -> str:
        """Few-line marker for main-agent context."""
        lines = [
            f"request_id={self.id} job_id={self.job_id} name={self.job_name or self.job_id}",
            f"question={self.question}",
        ]
        if self.options:
            lines.append("options=" + " | ".join(str(o) for o in self.options[:6]))
        if self.context_snippet:
            snippet = self.context_snippet
            # Origin pack can be longer so main knows what the job was building
            if len(snippet) > 900:
                snippet = snippet[:897] + "..."
            lines.append(f"context={snippet}")
        if self.require_user:
            lines.append("require_user=true — must ask the user, do not invent")
        elif self.allow_main_self_answer:
            lines.append(
                "hint=if answer is in origin pack/memory/tools confidently, call "
                "manage_background_tasks answer now with source main; do NOT narrate "
                "this Q&A to the user unless they must decide; else ask user"
            )
        if self.default_if_timeout:
            lines.append(f"default_if_timeout={self.default_if_timeout}")
        return "\n".join(lines)


@dataclass
class ClarificationResponse:
    request_id: str
    job_id: str
    answer: str
    source: str  # main | user | timeout | cancelled
    answered_at: datetime = field(default_factory=datetime.now)


class ClarificationBridge:
    """Singleton store + waiters for clarification Q&A."""

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
        self._requests: Dict[str, ClarificationRequest] = {}
        self._by_job: Dict[str, str] = {}  # job_id -> active request_id
        self._events: Dict[str, threading.Event] = {}
        self._job_counts: Dict[str, int] = {}
        self._wake_queue: List[str] = []  # request ids awaiting main wake
        self._load()
        # After process restart, no live waiters — reclaim pending as cancelled.
        self._reclaim_orphans()
        self._initialized = True

    def _load(self):
        try:
            if not CLARIFY_STORE_PATH.exists():
                return
            data = json.loads(CLARIFY_STORE_PATH.read_text(encoding="utf-8"))
            for raw in data.get("requests", []):
                req = ClarificationRequest.from_dict(raw)
                self._requests[req.id] = req
                if req.status == "pending":
                    self._by_job[req.job_id] = req.id
            for job_id, count in (data.get("job_counts") or {}).items():
                self._job_counts[job_id] = int(count)
        except Exception as e:
            print(f"[Clarify] Could not load store: {e}")

    def _persist(self):
        try:
            CLARIFY_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
            # Keep pending + recent answered (trim old answered to last 50)
            pending = [r for r in self._requests.values() if r.status == "pending"]
            closed = sorted(
                [r for r in self._requests.values() if r.status != "pending"],
                key=lambda r: r.answered_at or r.created_at,
                reverse=True,
            )[:50]
            keep = pending + closed
            payload = {
                "updated_at": datetime.now().isoformat(),
                "requests": [r.to_dict() for r in keep],
                "job_counts": dict(self._job_counts),
            }
            CLARIFY_STORE_PATH.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            # Drop closed that were not kept from memory
            keep_ids = {r.id for r in keep}
            for rid in list(self._requests.keys()):
                if rid not in keep_ids:
                    del self._requests[rid]
        except Exception as e:
            print(f"[Clarify] Could not save store: {e}")

    def _reclaim_orphans(self):
        """No live waiters after restart — fail open pending requests."""
        changed = False
        with self._lock:
            for req in list(self._requests.values()):
                if req.status != "pending":
                    continue
                req.status = "cancelled"
                req.source = "cancelled"
                req.answer = (
                    req.default_if_timeout
                    or "Clarification cancelled: process restarted while waiting."
                )
                req.answered_at = datetime.now()
                self._by_job.pop(req.job_id, None)
                changed = True
            if changed:
                self._persist()

    def get(self, request_id: str) -> Optional[ClarificationRequest]:
        with self._lock:
            return self._requests.get(request_id)

    def get_pending_for_job(self, job_id: str) -> Optional[ClarificationRequest]:
        with self._lock:
            rid = self._by_job.get(job_id)
            if not rid:
                return None
            req = self._requests.get(rid)
            if req and req.status == "pending":
                return req
            return None

    def list_pending(self) -> List[ClarificationRequest]:
        with self._lock:
            pending = [r for r in self._requests.values() if r.status == "pending"]
            pending.sort(key=lambda r: r.created_at.timestamp(), reverse=True)
            return pending

    def count_pending(self) -> int:
        with self._lock:
            return sum(1 for r in self._requests.values() if r.status == "pending")

    def prompt_text(self, max_items: int = 3) -> str:
        """Compact list for system-prompt injection at conversation start."""
        pending = self.list_pending()
        if not pending:
            return ""
        lines = []
        for req in pending[:max_items]:
            q = req.question
            if len(q) > 140:
                q = q[:137] + "..."
            name = req.job_name or req.job_id
            lines.append(
                f"[NEEDS INPUT] {name}: {q} "
                f"(request_id: {req.id}, job_id: {req.job_id})"
            )
        remaining = len(pending) - max_items
        if remaining > 0:
            lines.append(f"...and {remaining} more task(s) waiting on input")
        return "\n".join(lines)

    def has_wakes(self) -> bool:
        with self._lock:
            return bool(self._wake_queue)

    def pop_wakes(self) -> List[ClarificationRequest]:
        """Requests that still need a [TASK NEEDS INPUT] inject to main."""
        with self._lock:
            out: List[ClarificationRequest] = []
            remaining: List[str] = []
            for rid in self._wake_queue:
                req = self._requests.get(rid)
                if not req or req.status != "pending":
                    continue
                if req.woken:
                    continue
                req.woken = True
                out.append(req)
            self._wake_queue = remaining
            if out:
                self._persist()
            return out

    def requeue_wake(self, request_id: str):
        """Put a pending request back on the wake queue (e.g. deferred)."""
        with self._lock:
            req = self._requests.get(request_id)
            if not req or req.status != "pending":
                return
            req.woken = False
            if request_id not in self._wake_queue:
                self._wake_queue.append(request_id)
            self._persist()

    def job_clarification_count(self, job_id: str) -> int:
        with self._lock:
            return self._job_counts.get(job_id, 0)

    def ask_main(
        self,
        job_id: str,
        question: str,
        options: Optional[List[str]] = None,
        context_snippet: str = "",
        default_if_timeout: Optional[str] = None,
        require_user: bool = False,
        allow_main_self_answer: bool = True,
        timeout_seconds: Optional[int] = None,
        job_name: str = "",
        job_kind: str = "long",
    ) -> ClarificationResponse:
        """
        Block this thread until an answer, timeout, or cancel.

        One pending clarification per job. Raises ValueError if the job already
        has a pending request or has hit the max clarification budget.
        """
        question = (question or "").strip()
        if not question:
            return ClarificationResponse(
                request_id="",
                job_id=job_id,
                answer=default_if_timeout or "",
                source="timeout",
            )

        snippet = (context_snippet or "")[:300]
        if timeout_seconds is None:
            timeout_seconds = (
                DEFAULT_TIMEOUT_LONG if job_kind == "long" else DEFAULT_TIMEOUT_SHORT
            )
        timeout_seconds = max(1, int(timeout_seconds))

        with self._lock:
            if job_id in self._by_job:
                existing = self._requests.get(self._by_job[job_id])
                if existing and existing.status == "pending":
                    raise ValueError(
                        f"Job {job_id} already has a pending clarification "
                        f"({existing.id})"
                    )

            count = self._job_counts.get(job_id, 0)
            if count >= MAX_CLARIFICATIONS_PER_JOB:
                return ClarificationResponse(
                    request_id="",
                    job_id=job_id,
                    answer=(
                        default_if_timeout
                        or "Clarification budget exceeded; continue with best effort."
                    ),
                    source="timeout",
                )

            request_id = str(uuid.uuid4())[:8]
            req = ClarificationRequest(
                id=request_id,
                job_id=job_id,
                question=question,
                options=list(options) if options else None,
                context_snippet=snippet,
                default_if_timeout=default_if_timeout,
                allow_main_self_answer=allow_main_self_answer,
                require_user=require_user,
                timeout_seconds=timeout_seconds,
                job_name=job_name or "",
            )
            self._requests[request_id] = req
            self._by_job[job_id] = request_id
            self._job_counts[job_id] = count + 1
            event = threading.Event()
            self._events[request_id] = event
            self._wake_queue.append(request_id)
            self._persist()

        self._mark_job_needs_input(job_id, request_id)
        self._desktop_notify(req)

        finished = event.wait(timeout=timeout_seconds)

        with self._lock:
            req = self._requests.get(request_id)
            if not req:
                self._clear_job_needs_input(job_id)
                return ClarificationResponse(
                    request_id=request_id,
                    job_id=job_id,
                    answer=default_if_timeout or "No clarification response.",
                    source="timeout",
                )

            if finished and req.status == "answered":
                response = ClarificationResponse(
                    request_id=request_id,
                    job_id=job_id,
                    answer=req.answer or "",
                    source=req.source or "main",
                    answered_at=req.answered_at or datetime.now(),
                )
            elif finished and req.status == "cancelled":
                response = ClarificationResponse(
                    request_id=request_id,
                    job_id=job_id,
                    answer=req.answer
                    or default_if_timeout
                    or "Clarification cancelled.",
                    source="cancelled",
                    answered_at=req.answered_at or datetime.now(),
                )
            else:
                # Timeout path
                if req.status == "pending":
                    req.status = "timeout"
                    req.source = "timeout"
                    req.answer = (
                        default_if_timeout
                        if default_if_timeout is not None
                        else "TIMEOUT: no answer received."
                    )
                    req.answered_at = datetime.now()
                    self._by_job.pop(job_id, None)
                    self._persist()
                response = ClarificationResponse(
                    request_id=request_id,
                    job_id=job_id,
                    answer=req.answer or "",
                    source=req.source or "timeout",
                    answered_at=req.answered_at or datetime.now(),
                )

            self._events.pop(request_id, None)

        self._clear_job_needs_input(job_id)
        return response

    def answer(
        self,
        answer: str,
        request_id: str = "",
        task_id: str = "",
        source: str = "user",
    ) -> str:
        """
        Resume a waiting worker. Prefer request_id; fall back to task_id's
        pending request. source should be "main" or "user".
        """
        answer = (answer or "").strip()
        if not answer:
            return "Please provide a non-empty answer."

        with self._lock:
            req = None
            if request_id:
                req = self._requests.get(request_id.strip())
            if not req and task_id:
                rid = self._by_job.get(task_id.strip())
                if rid:
                    req = self._requests.get(rid)
            # Also allow task_id to be used as request_id by mistake
            if not req and task_id:
                req = self._requests.get(task_id.strip())

            if not req:
                return "No pending clarification found for that id."
            if req.status != "pending":
                return (
                    f"Clarification {req.id} is already {req.status} "
                    f"(source={req.source})."
                )

            req.status = "answered"
            req.answer = answer
            req.source = source if source in ("main", "user") else "user"
            req.answered_at = datetime.now()
            self._by_job.pop(req.job_id, None)
            event = self._events.get(req.id)
            self._persist()

        if event:
            event.set()
        return (
            f"Answer delivered to job {req.job_id} "
            f"(request {req.id}, source={req.source})."
        )

    def cancel_for_job(self, job_id: str, reason: str = "Job cancelled.") -> bool:
        with self._lock:
            rid = self._by_job.get(job_id)
            if not rid:
                return False
            req = self._requests.get(rid)
            if not req or req.status != "pending":
                return False
            req.status = "cancelled"
            req.source = "cancelled"
            req.answer = req.default_if_timeout or reason
            req.answered_at = datetime.now()
            self._by_job.pop(job_id, None)
            event = self._events.get(rid)
            self._persist()
        if event:
            event.set()
        return True

    def clear(self):
        """Test helper: wipe all in-memory and on-disk state."""
        with self._lock:
            for event in self._events.values():
                event.set()
            self._requests.clear()
            self._by_job.clear()
            self._events.clear()
            self._job_counts.clear()
            self._wake_queue.clear()
            self._persist()

    @staticmethod
    def _mark_job_needs_input(job_id: str, request_id: str):
        try:
            from core.TaskManager import TaskRegistry, TaskStatus

            registry = TaskRegistry()
            with registry._tasks_lock:
                task = registry._tasks.get(job_id)
                if task:
                    task.status = TaskStatus.NEEDS_INPUT
                    task.pending_request_id = request_id
                    registry._persist_locked()
        except Exception as e:
            print(f"[Clarify] Could not set needs_input: {e}")

    @staticmethod
    def _clear_job_needs_input(job_id: str):
        try:
            from core.TaskManager import TaskRegistry, TaskStatus

            registry = TaskRegistry()
            with registry._tasks_lock:
                task = registry._tasks.get(job_id)
                if not task:
                    return
                # Only flip back if still waiting; cancel/complete leave their status.
                if task.status == TaskStatus.NEEDS_INPUT:
                    task.status = TaskStatus.RUNNING
                task.pending_request_id = None
                registry._persist_locked()
        except Exception as e:
            print(f"[Clarify] Could not clear needs_input: {e}")

    @staticmethod
    def _desktop_notify(req: ClarificationRequest):
        try:
            from functions.system.notifiers import send_notification

            title = "AVA — Task needs input"
            body = f"{req.job_name or req.job_id}: {req.question}"[:160]
            send_notification(title, body)
        except Exception as e:
            print(f"[Clarify] Desktop notify failed: {e}")


def get_clarification_bridge() -> ClarificationBridge:
    return ClarificationBridge()
