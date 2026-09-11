"""
executor.py - Job concurrency caps, cancel events, orphan reclaim.

Phase 4 runtime spine (keeps TaskManager agents thin):
  - Max concurrent background jobs (AVA_MAX_BG_JOBS, default 4)
  - Per-job threading.Event for cooperative cancel
  - Live thread registry for orphan detection after process restart
"""

from __future__ import annotations

import os
import threading
import time
from typing import Callable, Dict, Optional, Set


def _default_max_jobs() -> int:
    try:
        return max(1, min(32, int(os.getenv("AVA_MAX_BG_JOBS", "4"))))
    except ValueError:
        return 4


class JobExecutor:
    """Process-wide job runtime controls (singleton)."""

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
        self.max_jobs = _default_max_jobs()
        self._slots = threading.Semaphore(self.max_jobs)
        self._cancel_events: Dict[str, threading.Event] = {}
        self._live_threads: Dict[str, threading.Thread] = {}
        self._lock = threading.RLock()
        self._initialized = True

    def reset_for_tests(self, max_jobs: Optional[int] = None):
        """Test helper: drop all runtime state."""
        with self._lock:
            if max_jobs is not None:
                self.max_jobs = max(1, int(max_jobs))
            self._slots = threading.Semaphore(self.max_jobs)
            self._cancel_events.clear()
            self._live_threads.clear()

    def cancel_event(self, job_id: str) -> threading.Event:
        """Get or create the cancel event for a job."""
        with self._lock:
            if job_id not in self._cancel_events:
                self._cancel_events[job_id] = threading.Event()
            return self._cancel_events[job_id]

    def signal_cancel(self, job_id: str) -> bool:
        with self._lock:
            ev = self._cancel_events.get(job_id)
            if not ev:
                # Still create so a late worker will see it.
                ev = threading.Event()
                self._cancel_events[job_id] = ev
            ev.set()
            return True

    def is_cancel_signaled(self, job_id: str) -> bool:
        with self._lock:
            ev = self._cancel_events.get(job_id)
            return bool(ev and ev.is_set())

    def clear_cancel(self, job_id: str):
        with self._lock:
            self._cancel_events.pop(job_id, None)

    def live_job_ids(self) -> Set[str]:
        with self._lock:
            return {
                jid
                for jid, th in self._live_threads.items()
                if th is not None and th.is_alive()
            }

    def is_live(self, job_id: str) -> bool:
        with self._lock:
            th = self._live_threads.get(job_id)
            return bool(th and th.is_alive())

    def try_acquire_slot(self, timeout: float = 0.0) -> bool:
        """Non-blocking (or timed) acquire of a concurrency slot."""
        return self._slots.acquire(timeout=max(0.0, float(timeout)))

    def release_slot(self):
        try:
            self._slots.release()
        except ValueError:
            # Released more than acquired — ignore in edge cleanup.
            pass

    def active_count(self) -> int:
        """Approximate number of running slots held (max - available)."""
        # Semaphore doesn't expose count portably; use live threads.
        return len(self.live_job_ids())

    def start_job(
        self,
        job_id: str,
        target: Callable,
        *,
        kwargs: Optional[dict] = None,
        acquire_timeout: float = 2.0,
        daemon: bool = True,
    ) -> bool:
        """
        Acquire a slot, register cancel event + live thread, start target.

        Returns False if concurrency cap blocked start (slot not acquired).
        ``target`` should call ``finish_job(job_id)`` in a finally block, or
        use ``run_wrapped``.
        """
        if not job_id:
            return False
        if not self.try_acquire_slot(timeout=acquire_timeout):
            return False

        self.cancel_event(job_id)  # ensure event exists (clear prior signal)

        def _wrapper():
            try:
                target(**(kwargs or {}))
            finally:
                self.finish_job(job_id)

        thread = threading.Thread(target=_wrapper, daemon=daemon, name=f"ava-job-{job_id}")
        with self._lock:
            self._live_threads[job_id] = thread
        thread.start()
        return True

    def finish_job(self, job_id: str):
        """Release slot + drop live-thread entry (cancel event kept until clear)."""
        with self._lock:
            self._live_threads.pop(job_id, None)
        self.release_slot()

    def reclaim_orphans(self, registry) -> list:
        """
        Mark PENDING / RUNNING / NEEDS_INPUT tasks with no live thread as dead.

        - running / needs_input → FAILED (worker was mid-flight)
        - pending → CANCELLED (never started, or spawn aborted across restart)

        Call once at process startup after TaskRegistry loads from disk.
        Returns list of reclaimed task ids.
        """
        reclaimed = []
        try:
            tasks = registry.get_all_tasks()
        except Exception:
            return reclaimed

        # Compare by value string to avoid import cycles during TaskManager load.
        live = self.live_job_ids()
        for task in tasks:
            status_val = getattr(task.status, "value", str(task.status))
            if status_val not in ("running", "needs_input", "pending"):
                continue
            if task.id in live:
                continue

            # If task is owned by another active Python process on this machine, do not kill it!
            meta = getattr(task, "metadata", {}) or {}
            task_pid = meta.get("pid")
            if task_pid and task_pid != os.getpid():
                try:
                    import psutil
                    if psutil.pid_exists(task_pid):
                        proc = psutil.Process(task_pid)
                        if proc.is_running() and "python" in proc.name().lower():
                            continue
                except Exception:
                    pass
            try:
                from core.TaskManager import TaskStatus

                if status_val == "pending":
                    new_status = TaskStatus.CANCELLED
                    err = "Abandoned pending job (never started or process restarted)."
                else:
                    new_status = TaskStatus.FAILED
                    err = "Orphaned after process restart (worker thread gone)."
                registry.update_task(task.id, new_status, error=err)
                with registry._tasks_lock:
                    t = registry._tasks.get(task.id)
                    if t:
                        t.pending_request_id = None
                        registry._persist_locked()
                self.clear_cancel(task.id)
                reclaimed.append(task.id)
            except Exception as e:
                print(f"[JobExecutor] Orphan reclaim failed for {task.id}: {e}")
        if reclaimed:
            print(f"[JobExecutor] Reclaimed orphan jobs: {reclaimed}")
        return reclaimed


def get_executor() -> JobExecutor:
    return JobExecutor()
