"""
sys_sentinel.py - Local hardware and runaway process sentinel.

Logs CPU and RAM telemetry continuously into the Blackboard Flight Recorder.
Raises a high-priority alert if CPU or RAM exceeds safety thresholds.
Supports both psutil and pure-Windows ctypes fallback.
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
import time
from typing import Any, Dict, Optional, Tuple

from services.observers.base_sentinel import BaseSentinel

logger = logging.getLogger("AvaSysSentinel")

try:
    import psutil
    _HAS_PSUTIL = True
except ImportError:
    _HAS_PSUTIL = False


# Windows ctypes memory status fallback
class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def _get_windows_mem_stats() -> Tuple[float, float, float]:
    """Returns (percent_used, used_gb, total_gb)."""
    try:
        stat = _MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            total_gb = stat.ullTotalPhys / (1024 ** 3)
            avail_gb = stat.ullAvailPhys / (1024 ** 3)
            used_gb = total_gb - avail_gb
            return float(stat.dwMemoryLoad), round(used_gb, 2), round(total_gb, 2)
    except Exception:
        pass
    return 0.0, 0.0, 0.0


class SystemSentinel(BaseSentinel):
    """Monitors CPU and RAM loads and logs to Blackboard flight recorder."""

    def __init__(self, interval_sec: float = 15.0):
        super().__init__(name="system_sentinel", interval_sec=interval_sec)
        self.cpu_alert_threshold = 95.0
        self.ram_alert_threshold = 92.0

    async def check(self) -> Optional[Dict[str, Any]]:
        global _HAS_PSUTIL
        if not _HAS_PSUTIL:
            try:
                import psutil
                _HAS_PSUTIL = True
            except ImportError:
                pass

        cpu_percent = 0.0
        ram_percent = 0.0
        ram_used_gb = 0.0
        ram_total_gb = 0.0

        if _HAS_PSUTIL:
            try:
                cpu_percent = psutil.cpu_percent(interval=None)
                mem = psutil.virtual_memory()
                ram_percent = mem.percent
                ram_used_gb = round((mem.total - mem.available) / (1024 ** 3), 2)
                ram_total_gb = round(mem.total / (1024 ** 3), 2)
            except Exception as e:
                logger.debug(f"[SystemSentinel] psutil error: {e}")
        else:
            ram_percent, ram_used_gb, ram_total_gb = _get_windows_mem_stats()

        is_critical = (cpu_percent >= self.cpu_alert_threshold) or (
            ram_percent >= self.ram_alert_threshold
        )
        is_warn = (cpu_percent >= 85.0) or (ram_percent >= 85.0)

        severity = "CRITICAL" if is_critical else ("WARN" if is_warn else "INFO")

        data: Dict[str, Any] = {
            "cpu_percent": cpu_percent,
            "ram_percent": ram_percent,
            "ram_used_gb": ram_used_gb,
            "ram_total_gb": ram_total_gb,
            "severity": severity,
            "is_alert": is_critical,
        }

        if is_critical:
            reason = []
            if cpu_percent >= self.cpu_alert_threshold:
                reason.append(f"CPU sustained at {cpu_percent}%")
            if ram_percent >= self.ram_alert_threshold:
                reason.append(f"RAM capacity at {ram_percent}% ({ram_used_gb}GB / {ram_total_gb}GB)")
            data["title"] = "Hardware Threshold Alert"
            data["message"] = f"Critical load: {', '.join(reason)}."

        return data
