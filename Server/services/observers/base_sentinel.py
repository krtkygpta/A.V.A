"""
base_sentinel.py - Base daemon class for continuous personal sentinels.

Features:
- Continuous silent flight-recording into Blackboard.telemetry_ledger
- Anti-flapping sliding window / hysteresis to prevent transient noise
- Catastrophic interrupt detection with attached historical flight recorder
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any, Dict, List, Optional

from services.blackboard import blackboard

logger = logging.getLogger("AvaSentinel")


class BaseSentinel:
    """Base class for continuous background sentinels."""

    def __init__(self, name: str, interval_sec: float = 15.0):
        self.name = name
        self.interval_sec = interval_sec
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._consecutive_breaches = 0
        self._breach_threshold = 3  # Hysteresis: condition must hold for N checks

    async def check(self) -> Optional[Dict[str, Any]]:
        """
        Perform one inspection cycle.
        Should return a dictionary containing telemetry data.
        If an anomaly is detected, include 'is_alert': True and 'severity': 'CRITICAL' | 'WARN'.
        """
        raise NotImplementedError

    async def _run(self) -> None:
        logger.info(f"[Sentinel:{self.name}] Started background monitoring loop (interval={self.interval_sec}s).")
        while self._running:
            try:
                result = await self.check()
                if result:
                    level = result.get("severity", "INFO").upper()
                    is_alert = result.get("is_alert", False)

                    # 1. Continuous silent flight recording
                    blackboard.record_telemetry(
                        sentinel=self.name,
                        data=result,
                        level=level,
                    )

                    # 2. Hysteresis check for catastrophic / urgent alerts
                    if is_alert:
                        self._consecutive_breaches += 1
                        if self._consecutive_breaches >= self._breach_threshold:
                            # Trigger catastrophic interrupt with 10-minute flight recorder attached
                            history = blackboard.get_telemetry_history(
                                sentinel=self.name, limit=20
                            )
                            alert_payload = {
                                "sentinel": self.name,
                                "alert_title": result.get("title", f"Alert from {self.name}"),
                                "message": result.get("message", ""),
                                "severity": level,
                                "flight_recorder_snapshot": history,
                                "timestamp": time.time(),
                            }
                            logger.warning(
                                f"[Sentinel:{self.name}] CATASTROPHIC BREACH CONFIRMED: {alert_payload['alert_title']}"
                            )
                            # Emit through blackboard so Layer 2 / Layer 1 receives it
                            blackboard._emit("sentinel_alert", alert_payload)
                            # Reset count to prevent spamming every cycle while still alert
                            self._consecutive_breaches = 0
                    else:
                        self._consecutive_breaches = max(0, self._consecutive_breaches - 1)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[Sentinel:{self.name}] Error during check: {e}")

            await asyncio.sleep(self.interval_sec)

        logger.info(f"[Sentinel:{self.name}] Stopped monitoring loop.")

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        loop = asyncio.get_event_loop()
        self._task = loop.create_task(self._run())

    def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
