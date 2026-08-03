"""
base.py - Service contracts: ServiceEvent, EventSink, AvaService protocol.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Protocol, runtime_checkable


@dataclass
class ServiceEvent:
    """One signal from an always-on service into the shared notify/inbox path."""

    service: str
    event_type: str
    title: str = ""
    summary: str = ""
    severity: str = "normal"  # critical | important | normal | low
    artifact_path: Optional[str] = None
    create_inbox: bool = False
    speak_now: bool = False
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    created_at: datetime = field(default_factory=datetime.now)
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "service": self.service,
            "event_type": self.event_type,
            "title": self.title,
            "summary": self.summary,
            "severity": self.severity,
            "artifact_path": self.artifact_path,
            "create_inbox": self.create_inbox,
            "speak_now": self.speak_now,
            "created_at": self.created_at.isoformat(),
            "metadata": self.metadata,
        }


class EventSink:
    """Default sink: route ServiceEvent through jobs.notify."""

    def emit(self, event: ServiceEvent) -> None:
        from core.jobs.notify import route_service_event

        route_service_event(event)


@runtime_checkable
class AvaService(Protocol):
    name: str

    def start(self, sink: EventSink, stop_event: threading.Event) -> None:
        """Begin work (may spawn a daemon thread). Must not block forever in start."""
        ...

    def stop(self) -> None:
        """Signal the service to halt; join with a short timeout if needed."""
        ...
