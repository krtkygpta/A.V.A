"""
health_monitor.py - Stub HealthMonitor service.

Reads a mock heart-rate value from ``App/data/services/hr.txt`` (plain number)
or uses an internal counter. Emits a **critical** inbox + speak event when HR
exceeds ``threshold`` (default 140). Threshold is code/config — not LLM.

Enable via AVA_SERVICES=health_monitor or services config.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Optional

from core.services.base import EventSink, ServiceEvent

HR_FILE = (
    Path(__file__).resolve().parent.parent.parent / "data" / "services" / "hr.txt"
)


class HealthMonitor:
    name = "health_monitor"

    def __init__(
        self,
        threshold: int = 140,
        poll_seconds: float = 5.0,
        *,
        mock_path: Optional[Path] = None,
    ):
        self.threshold = int(os.getenv("AVA_HR_THRESHOLD", threshold))
        self.poll_seconds = max(1.0, float(poll_seconds))
        self.mock_path = mock_path or HR_FILE
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._sink: Optional[EventSink] = None
        self._last_alert_at = 0.0
        self._alert_cooldown = 60.0  # don't spam critical more than once/min

    def start(self, sink: EventSink, stop_event: threading.Event) -> None:
        self._sink = sink
        self._stop = stop_event
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._loop, name="ava-health-monitor", daemon=True
        )
        self._thread.start()
        print(f"[Services] HealthMonitor started (threshold={self.threshold})")

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def _read_hr(self) -> Optional[float]:
        try:
            if self.mock_path.exists():
                raw = self.mock_path.read_text(encoding="utf-8").strip().split()[0]
                return float(raw)
        except Exception:
            pass
        return None

    def _loop(self):
        while not self._stop.is_set():
            try:
                hr = self._read_hr()
                if hr is not None and hr >= self.threshold:
                    now = time.time()
                    if now - self._last_alert_at >= self._alert_cooldown:
                        self._last_alert_at = now
                        if self._sink:
                            self._sink.emit(
                                ServiceEvent(
                                    service=self.name,
                                    event_type="hr_alert",
                                    title="Heart rate high",
                                    summary=f"HR {hr:.0f} ≥ threshold {self.threshold}",
                                    severity="critical",
                                    create_inbox=True,
                                    speak_now=True,
                                    metadata={"hr": hr, "threshold": self.threshold},
                                )
                            )
            except Exception as e:
                print(f"[HealthMonitor] loop error: {e}")
            self._stop.wait(self.poll_seconds)

    def emit_test_alert(self, hr: float = 160.0) -> None:
        """Test helper: force a critical event without waiting for the file."""
        if not self._sink:
            self._sink = EventSink()
        self._sink.emit(
            ServiceEvent(
                service=self.name,
                event_type="hr_alert",
                title="Heart rate high",
                summary=f"HR {hr:.0f} ≥ threshold {self.threshold}",
                severity="critical",
                create_inbox=True,
                speak_now=True,
                metadata={"hr": hr, "threshold": self.threshold, "test": True},
            )
        )
