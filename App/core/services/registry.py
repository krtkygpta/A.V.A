"""
registry.py - Start/stop always-on Ava services from config.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Dict, List, Optional

from core.services.base import AvaService, EventSink

CONFIG_PATH = (
    Path(__file__).resolve().parent.parent.parent / "data" / "services" / "config.json"
)

# Built-in factories
_BUILTIN = {
    "health_monitor": "core.services.health_monitor:HealthMonitor",
    "call_manager": "core.services.call_manager:CallManager",
}


def _load_enabled_names() -> List[str]:
    # Env wins: AVA_SERVICES=health_monitor,call_manager  or "none" / "off"
    env = os.getenv("AVA_SERVICES", "").strip()
    if env.lower() in ("none", "off", "0", "false"):
        return []
    if env:
        return [n.strip() for n in env.split(",") if n.strip()]

    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            enabled = data.get("enabled") or data.get("services") or []
            if isinstance(enabled, dict):
                return [k for k, v in enabled.items() if v]
            return [str(x) for x in enabled]
        except Exception as e:
            print(f"[Services] Config load failed: {e}")

    # Default: both stubs on (cheap; HealthMonitor only alerts when hr.txt high)
    return ["health_monitor", "call_manager"]


def _import_service(path: str):
    module_name, _, cls_name = path.partition(":")
    import importlib

    mod = importlib.import_module(module_name)
    return getattr(mod, cls_name)


class ServiceRegistry:
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
        self._services: Dict[str, AvaService] = {}
        self._stop_events: Dict[str, threading.Event] = {}
        self._sink = EventSink()
        self._started = False
        self._initialized = True

    def get(self, name: str) -> Optional[AvaService]:
        return self._services.get(name)

    def list_running(self) -> List[str]:
        return list(self._services.keys())

    def start_all(self, names: Optional[List[str]] = None) -> List[str]:
        """Start enabled services. Idempotent. Returns names started this call."""
        if names is None:
            names = _load_enabled_names()
        started = []
        for name in names:
            if name in self._services:
                continue
            spec = _BUILTIN.get(name)
            if not spec:
                print(f"[Services] Unknown service '{name}' — skip")
                continue
            try:
                cls = _import_service(spec)
                svc = cls()
                stop_ev = threading.Event()
                svc.start(self._sink, stop_ev)
                self._services[name] = svc
                self._stop_events[name] = stop_ev
                started.append(name)
            except Exception as e:
                print(f"[Services] Failed to start {name}: {e}")
        self._started = True
        return started

    def stop_all(self):
        for name, svc in list(self._services.items()):
            try:
                ev = self._stop_events.get(name)
                if ev:
                    ev.set()
                svc.stop()
            except Exception as e:
                print(f"[Services] Stop failed for {name}: {e}")
        self._services.clear()
        self._stop_events.clear()
        self._started = False

    def reset_for_tests(self):
        self.stop_all()


def get_service_registry() -> ServiceRegistry:
    return ServiceRegistry()


def start_ava_services() -> List[str]:
    return get_service_registry().start_all()


def stop_ava_services():
    get_service_registry().stop_all()
