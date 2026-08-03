"""
call_manager.py - Stub CallManager service.

v1: no real telephony. Provides:
  - emit_call_needs_user(summary) → important + inbox + speak
  - emit_call_summary(summary) → normal + inbox

Custom code / future phone integration can call these hooks.
"""

from __future__ import annotations

import threading
from typing import Optional

from core.services.base import EventSink, ServiceEvent


class CallManager:
    name = "call_manager"

    def __init__(self):
        self._stop = threading.Event()
        self._sink: Optional[EventSink] = None

    def start(self, sink: EventSink, stop_event: threading.Event) -> None:
        self._sink = sink
        self._stop = stop_event
        print("[Services] CallManager stub started")

    def stop(self) -> None:
        self._stop.set()

    def _emit(self, event: ServiceEvent) -> None:
        sink = self._sink or EventSink()
        sink.emit(event)

    def emit_call_needs_user(self, summary: str, title: str = "Call needs you") -> None:
        self._emit(
            ServiceEvent(
                service=self.name,
                event_type="call_needs_user",
                title=title,
                summary=summary,
                severity="important",
                create_inbox=True,
                speak_now=True,
            )
        )

    def emit_call_summary(self, summary: str, title: str = "Call summary") -> None:
        self._emit(
            ServiceEvent(
                service=self.name,
                event_type="call_summary",
                title=title,
                summary=summary,
                severity="normal",
                create_inbox=True,
                speak_now=False,
            )
        )
