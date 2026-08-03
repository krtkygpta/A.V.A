"""
resources.py - Exclusive resource lanes for short background jobs.

Smarthome (and similar device) batches should not interleave: one holder at a
time. Jobs acquire a lane for the duration of execute(); if a job later enters
needs_input, it should release the lane (re-acquire after resume if needed).
"""

from __future__ import annotations

import threading
import time
from typing import Dict, Optional


class ExclusiveLane:
    """Named mutual-exclusion lane (one job_id holder at a time)."""

    def __init__(self, name: str):
        self.name = name
        self._lock = threading.Lock()
        self._holder: Optional[str] = None
        self._acquired_at: Optional[float] = None

    def acquire(self, job_id: str, timeout: float = 120.0) -> bool:
        """
        Block until the lane is free or timeout expires.
        Same job_id re-acquiring while already holder is a no-op success.
        """
        if not job_id:
            return False
        deadline = time.time() + max(0.0, float(timeout))
        while True:
            with self._lock:
                if self._holder is None or self._holder == job_id:
                    self._holder = job_id
                    self._acquired_at = time.time()
                    return True
            if time.time() >= deadline:
                return False
            time.sleep(0.05)

    def release(self, job_id: str) -> bool:
        """Release only if job_id is the current holder."""
        with self._lock:
            if self._holder != job_id:
                return False
            self._holder = None
            self._acquired_at = None
            return True

    def holder(self) -> Optional[str]:
        with self._lock:
            return self._holder

    def is_held(self) -> bool:
        with self._lock:
            return self._holder is not None

    def force_release(self):
        """Test/admin helper."""
        with self._lock:
            self._holder = None
            self._acquired_at = None


class ResourceRegistry:
    """Process-wide named lanes."""

    _instance = None
    _init_lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._lanes: Dict[str, ExclusiveLane] = {
            "smarthome": ExclusiveLane("smarthome"),
        }
        self._lock = threading.Lock()
        self._initialized = True

    def get(self, name: str) -> ExclusiveLane:
        with self._lock:
            if name not in self._lanes:
                self._lanes[name] = ExclusiveLane(name)
            return self._lanes[name]

    def reset(self):
        """Test helper: clear all holders."""
        with self._lock:
            for lane in self._lanes.values():
                lane.force_release()


def get_lane(name: str = "smarthome") -> ExclusiveLane:
    return ResourceRegistry().get(name)


def get_resource_registry() -> ResourceRegistry:
    return ResourceRegistry()
