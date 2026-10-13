"""
blackboard.py - Central in-memory state machine, live thought stream, and Sentinel Flight Recorder.

Serves as the high-speed synchronization plane between:
  - Layer 1 (Executive Avatar): Fast status lookups & ticket generation
  - Layer 2 (Chief of Staff): Deliberative planning, step updates, subagent tracking
  - Sentinels: Continuous telemetry logging (Flight Recorder)
  - Clients: WebSocket streaming of live thinking traces
"""

from __future__ import annotations

import collections
import logging
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Callable, Deque, Dict, List, Optional

logger = logging.getLogger("AvaBlackboard")


@dataclass
class TaskStep:
    step_id: str
    title: str
    assigned_worker: str = "orchestrator"
    status: str = "pending"  # "pending", "running", "completed", "failed"
    started_at: Optional[float] = None
    ended_at: Optional[float] = None
    last_log_line: str = ""
    error_message: Optional[str] = None
    artifacts: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class JobTicket:
    ticket_id: str
    goal: str
    status: str = "scoping"  # "scoping", "in_progress", "blocked", "completed", "failed", "cancelled"
    client_id: str = "default"
    conversation_id: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    progress_pct: int = 0
    estimated_remaining_sec: int = 0
    active_blocker: Optional[str] = None
    steps: List[TaskStep] = field(default_factory=list)
    executive_summary: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ticket_id": self.ticket_id,
            "goal": self.goal,
            "status": self.status,
            "client_id": self.client_id,
            "conversation_id": self.conversation_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "progress_pct": self.progress_pct,
            "estimated_remaining_sec": self.estimated_remaining_sec,
            "active_blocker": self.active_blocker,
            "steps": [s.to_dict() for s in self.steps],
            "executive_summary": self.executive_summary,
            "metadata": dict(self.metadata),
        }



@dataclass
class TelemetryEntry:
    entry_id: str
    sentinel: str
    timestamp: float
    level: str  # "INFO", "WARN", "CRITICAL"
    data: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class Blackboard:
    """Singleton in-memory shared blackboard for A.V.A 2.0."""

    _instance: Optional[Blackboard] = None
    _init_lock = threading.Lock()

    def __new__(cls) -> Blackboard:
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if getattr(self, "_initialized", False):
            return
        self._lock = threading.RLock()
        self._tickets: Dict[str, JobTicket] = {}
        self._thought_logs: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
        # Flight Recorder: stores up to 2000 continuous sentinel telemetry breadcrumbs
        self._flight_recorder: Deque[TelemetryEntry] = collections.deque(maxlen=2000)
        self._listeners: List[Callable[[str, Dict[str, Any]], None]] = []
        self._initialized = True
        logger.info("[Blackboard] Central in-memory blackboard initialized.")

    # ── Listener / PubSub ───────────────────────────────────────────────────

    def add_listener(self, callback: Callable[[str, Dict[str, Any]], None]) -> None:
        """Register a callback for live event broadcasting (e.g. WebSocket push)."""
        with self._lock:
            if callback not in self._listeners:
                self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[str, Dict[str, Any]], None]) -> None:
        with self._lock:
            if callback in self._listeners:
                self._listeners.remove(callback)

    def _emit(self, event_type: str, payload: Dict[str, Any]) -> None:
        listeners = list(self._listeners)
        for cb in listeners:
            try:
                cb(event_type, payload)
            except Exception as e:
                logger.debug(f"[Blackboard] Listener error: {e}")

    # ── Job Ticket Management ───────────────────────────────────────────────

    def create_ticket(
        self,
        goal: str,
        client_id: str = "default",
        conversation_id: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> JobTicket:
        with self._lock:
            ticket_id = f"job_{uuid.uuid4().hex[:8]}"
            ticket = JobTicket(
                ticket_id=ticket_id,
                goal=goal,
                client_id=client_id,
                conversation_id=conversation_id,
                metadata=metadata or {},
            )
            self._tickets[ticket_id] = ticket
            self._emit("ticket_created", {"ticket": ticket.to_dict()})
            return ticket

    def get_ticket(self, ticket_id: str) -> Optional[JobTicket]:
        with self._lock:
            return self._tickets.get(ticket_id)

    def update_ticket_status(
        self,
        ticket_id: str,
        status: Optional[str] = None,
        progress_pct: Optional[int] = None,
        estimated_remaining_sec: Optional[int] = None,
        active_blocker: Optional[str] = None,
        executive_summary: Optional[str] = None,
    ) -> Optional[JobTicket]:
        with self._lock:
            ticket = self._tickets.get(ticket_id)
            if not ticket:
                return None
            ticket.updated_at = time.time()
            if status is not None:
                ticket.status = status
            if progress_pct is not None:
                ticket.progress_pct = max(0, min(100, progress_pct))
            if estimated_remaining_sec is not None:
                ticket.estimated_remaining_sec = estimated_remaining_sec
            if active_blocker is not None:
                ticket.active_blocker = active_blocker if active_blocker else None
            if executive_summary is not None:
                ticket.executive_summary = executive_summary

            self._emit("ticket_updated", {"ticket": ticket.to_dict()})
            return ticket

    def add_step(
        self,
        ticket_id: str,
        title: str,
        assigned_worker: str = "orchestrator",
    ) -> Optional[TaskStep]:
        with self._lock:
            ticket = self._tickets.get(ticket_id)
            if not ticket:
                return None
            step_id = f"step_{len(ticket.steps) + 1}_{uuid.uuid4().hex[:4]}"
            step = TaskStep(
                step_id=step_id,
                title=title,
                assigned_worker=assigned_worker,
                status="pending",
            )
            ticket.steps.append(step)
            ticket.updated_at = time.time()
            self._emit("step_added", {"ticket_id": ticket_id, "step": step.to_dict()})
            return step

    def update_step(
        self,
        ticket_id: str,
        step_id: str,
        status: Optional[str] = None,
        last_log_line: Optional[str] = None,
        error_message: Optional[str] = None,
        artifacts: Optional[List[str]] = None,
    ) -> Optional[TaskStep]:
        with self._lock:
            ticket = self._tickets.get(ticket_id)
            if not ticket:
                return None
            target_step = next((s for s in ticket.steps if s.step_id == step_id), None)
            if not target_step:
                return None

            now = time.time()
            if status:
                target_step.status = status
                if status == "running" and target_step.started_at is None:
                    target_step.started_at = now
                elif status in ("completed", "failed"):
                    target_step.ended_at = now
            if last_log_line is not None:
                target_step.last_log_line = last_log_line
            if error_message is not None:
                target_step.error_message = error_message
            if artifacts:
                target_step.artifacts.extend(artifacts)

            ticket.updated_at = now
            self._emit(
                "step_updated",
                {"ticket_id": ticket_id, "step": target_step.to_dict()},
            )
            return target_step

    def append_thought(self, ticket_id: str, text: str, stage: str = "thinking") -> None:
        """Stream an internal reasoning token or thought log."""
        with self._lock:
            entry = {
                "timestamp": time.time(),
                "stage": stage,
                "text": text,
            }
            self._thought_logs[ticket_id].append(entry)
            self._emit(
                "thinking",
                {
                    "ticket_id": ticket_id,
                    "stage": stage,
                    "text": text,
                    "timestamp": entry["timestamp"],
                },
            )

    def get_thoughts(self, ticket_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._thought_logs.get(ticket_id, []))

    def cancel_ticket(self, ticket_id: str) -> bool:
        with self._lock:
            ticket = self._tickets.get(ticket_id)
            if not ticket:
                return False
            ticket.status = "cancelled"
            ticket.cancel_event.set()
            ticket.updated_at = time.time()
            self._emit("ticket_cancelled", {"ticket_id": ticket_id})
            return True

    def list_tickets(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._lock:
            sorted_tickets = sorted(
                self._tickets.values(), key=lambda t: t.created_at, reverse=True
            )
            return [t.to_dict() for t in sorted_tickets[:limit]]

    def find_active_job_by_query(self, query: str) -> Optional[JobTicket]:
        """
        Fast in-memory semantic/keyword lookup for in-flight queries.
        Enables Layer 1 to answer 'How is task X going?' in <10ms.
        """
        with self._lock:
            q_lower = query.lower()
            # First check actively running/blocked jobs
            active = [
                t
                for t in self._tickets.values()
                if t.status in ("in_progress", "scoping", "blocked")
            ]
            if not active:
                # Fallback to most recent completed within last 3 minutes
                now = time.time()
                recent = [
                    t
                    for t in self._tickets.values()
                    if (now - t.updated_at) < 180
                ]
                active = recent

            if not active:
                return None

            # Score match
            for t in sorted(active, key=lambda x: x.updated_at, reverse=True):
                goal_words = set(t.goal.lower().split())
                query_words = set(q_lower.split())
                if goal_words & query_words or any(w in t.goal.lower() for w in query_words if len(w) > 3):
                    return t
            return active[0] if active else None

    # ── Sentinel Telemetry & Flight Recorder ────────────────────────────────

    def record_telemetry(
        self,
        sentinel: str,
        data: Dict[str, Any],
        level: str = "INFO",
    ) -> TelemetryEntry:
        """
        Append breadcrumb to circular Flight Recorder ring buffer.
        Never throws, zero external IO, instantaneous.
        """
        with self._lock:
            entry = TelemetryEntry(
                entry_id=f"rec_{uuid.uuid4().hex[:6]}",
                sentinel=sentinel,
                timestamp=time.time(),
                level=level.upper(),
                data=data,
            )
            self._flight_recorder.append(entry)
            self._emit("telemetry_recorded", {"entry": entry.to_dict()})
            return entry

    def get_telemetry_history(
        self,
        sentinel: Optional[str] = None,
        limit: int = 50,
        level: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Query recent flight recorder entries for diagnosis."""
        with self._lock:
            results = []
            for entry in reversed(self._flight_recorder):
                if sentinel and entry.sentinel != sentinel:
                    continue
                if level and entry.level != level.upper():
                    continue
                results.append(entry.to_dict())
                if len(results) >= limit:
                    break
            return results


# Global singleton access
blackboard = Blackboard()
