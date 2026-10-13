"""
device_registry.py - Central Device Hub & Tool Catalog for AVA Server.

Manages persistent WebSocket connections for all connected clients (Web, Android, PC/Linux),
tracks online/offline presence, resolves target devices, and routes RPC tool calls.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("DeviceRegistry")


@dataclass
class Device:
    device_id: str
    name: str
    device_type: str = "web"  # "web" | "android" | "windows" | "linux" | "macos"
    capabilities: List[str] = field(default_factory=list)
    tool_catalog: Dict[str, Any] = field(default_factory=dict)
    last_seen: float = field(default_factory=time.time)
    ws: Any = None  # WebSocket connection
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_online(self) -> bool:
        return self.ws is not None and (time.time() - self.last_seen) < 120.0

    def last_seen_text(self) -> str:
        if self.is_online:
            return "Online"
        diff = int(time.time() - self.last_seen)
        if diff < 60:
            return "Just now"
        elif diff < 3600:
            return f"{diff // 60}m ago"
        elif diff < 86400:
            return f"{diff // 3600}h ago"
        return f"{diff // 86400}d ago"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "device_id": self.device_id,
            "name": self.name,
            "device_type": self.device_type,
            "capabilities": self.capabilities,
            "tool_catalog": self.tool_catalog,
            "online": self.is_online,
            "last_seen": self.last_seen,
            "last_seen_text": self.last_seen_text(),
            "metadata": self.metadata,
        }


class DeviceRegistry:
    """Thread-safe registry for connected devices and tool routing."""

    _instance: Optional["DeviceRegistry"] = None
    _lock = threading.Lock()

    def __new__(cls) -> "DeviceRegistry":
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._devices: Dict[str, Device] = {}
                cls._instance._ws_to_id: Dict[Any, str] = {}
                cls._instance._pending_rpcs: Dict[str, asyncio.Future] = {}
                cls._instance._rw_lock = threading.Lock()
                cls._instance._event_loop: Optional[asyncio.AbstractEventLoop] = None
                cls._instance._persistence_file = Path(__file__).resolve().parent.parent / "data" / "devices.json"
                cls._instance._load_persisted()
            return cls._instance

    def _load_persisted(self) -> None:
        try:
            if self._persistence_file and self._persistence_file.exists():
                with open(self._persistence_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    for d_id, item in data.items():
                        if isinstance(item, dict) and d_id not in self._devices:
                            self._devices[d_id] = Device(
                                device_id=d_id,
                                name=item.get("name", d_id),
                                device_type=item.get("device_type", "other"),
                                capabilities=item.get("capabilities", []),
                                tool_catalog=item.get("tool_catalog", {}),
                                last_seen=item.get("last_seen", time.time()),
                                ws=None,
                                metadata=item.get("metadata", {}),
                            )
                    logger.info(f"[DeviceRegistry] Loaded {len(self._devices)} persisted devices from disk.")
        except Exception as e:
            logger.warning(f"[DeviceRegistry] Error loading persisted devices: {e}")

    def _save_persisted(self) -> None:
        try:
            if not self._persistence_file:
                return
            self._persistence_file.parent.mkdir(parents=True, exist_ok=True)
            dump_data = {}
            for d_id, dev in self._devices.items():
                if d_id.startswith(("test_", "tmp_", "sim_")):
                    continue
                dump_data[d_id] = {
                    "device_id": dev.device_id,
                    "name": dev.name,
                    "device_type": dev.device_type,
                    "capabilities": dev.capabilities,
                    "tool_catalog": dev.tool_catalog,
                    "last_seen": dev.last_seen,
                    "metadata": {k: v for k, v in dev.metadata.items() if k != "ws"},
                }
            with open(self._persistence_file, "w", encoding="utf-8") as f:
                json.dump(dump_data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"[DeviceRegistry] Error saving persisted devices: {e}")

    def set_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._event_loop = loop

    def register(
        self,
        device_id: str,
        name: Optional[str] = None,
        device_type: str = "web",
        capabilities: Optional[List[str]] = None,
        tool_catalog: Optional[Dict[str, Any]] = None,
        ws: Any = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Register or update a device with an optional active WebSocket connection."""
        clean_id = str(device_id).strip().lower()
        if not clean_id:
            raise ValueError("device_id cannot be empty")

        clean_name = str(name).strip() if name else clean_id
        caps = list(capabilities) if capabilities else []

        with self._rw_lock:
            if ws and ws in self._ws_to_id:
                old_id = self._ws_to_id.pop(ws, None)
                if old_id and old_id in self._devices and old_id != clean_id:
                    self._devices[old_id].ws = None

            existing = self._devices.get(clean_id)
            if existing:
                existing.name = clean_name
                existing.device_type = device_type or existing.device_type
                if capabilities is not None:
                    existing.capabilities = caps
                if tool_catalog:
                    existing.tool_catalog.update(tool_catalog)
                existing.last_seen = time.time()
                if ws is not None:
                    existing.ws = ws
                    self._ws_to_id[ws] = clean_id
                if metadata:
                    existing.metadata.update(metadata)
                dev = existing
            else:
                dev = Device(
                    device_id=clean_id,
                    name=clean_name,
                    device_type=device_type,
                    capabilities=caps,
                    tool_catalog=tool_catalog or {},
                    last_seen=time.time(),
                    ws=ws,
                    metadata=metadata or {},
                )
                self._devices[clean_id] = dev
                if ws is not None:
                    self._ws_to_id[ws] = clean_id

        if not clean_id.startswith(("test_", "tmp_", "sim_")):
            self._save_persisted()
        logger.info(f"[DeviceRegistry] Registered device '{clean_id}' ({clean_name}, {device_type}) with tools: {caps}")
        return dev.to_dict()

    def unregister_ws(self, ws: Any) -> Optional[str]:
        """Handle WebSocket disconnection. Marks device offline while preserving last_seen."""
        with self._rw_lock:
            dev_id = self._ws_to_id.pop(ws, None)
            if dev_id and dev_id in self._devices:
                dev = self._devices[dev_id]
                dev.ws = None
                dev.last_seen = time.time()
                if dev_id.startswith(("test_", "tmp_", "sim_")):
                    self._devices.pop(dev_id, None)
                else:
                    self._save_persisted()
                logger.info(f"[DeviceRegistry] Device '{dev_id}' disconnected (marked offline)")
                return dev_id
        return None

    def heartbeat(self, device_id: str) -> bool:
        clean_id = str(device_id).strip().lower()
        with self._rw_lock:
            dev = self._devices.get(clean_id)
            if dev:
                dev.last_seen = time.time()
                return True
        return False

    def get(self, device_id: str) -> Optional[Device]:
        clean_id = str(device_id).strip().lower()
        with self._rw_lock:
            return self._devices.get(clean_id)

    def list_devices(self, online_only: bool = False, include_ephemeral: bool = False) -> List[Dict[str, Any]]:
        with self._rw_lock:
            devices = list(self._devices.values())
        if not include_ephemeral:
            devices = [d for d in devices if not d.device_id.startswith(("test_", "tmp_", "sim_"))]
        if online_only:
            devices = [d for d in devices if d.is_online]
        return [d.to_dict() for d in devices]

    def rename_device(self, device_id: str, new_name: str) -> bool:
        clean_id = str(device_id).strip().lower()
        clean_name = str(new_name).strip()
        if not clean_name:
            return False
        with self._rw_lock:
            dev = self._devices.get(clean_id)
            if not dev:
                return False
            dev.name = clean_name
            self._save_persisted()
            logger.info(f"[DeviceRegistry] Renamed device '{clean_id}' to '{clean_name}'")
            return True

    def unregister_device(self, device_id: str) -> bool:
        clean_id = str(device_id).strip().lower()
        with self._rw_lock:
            dev = self._devices.pop(clean_id, None)
            if not dev:
                return False
            for ws, did in list(self._ws_to_id.items()):
                if did == clean_id:
                    self._ws_to_id.pop(ws, None)
            self._save_persisted()
            logger.info(f"[DeviceRegistry] Unregistered device '{clean_id}'")
            return True

    async def ping_device(self, device_id: str) -> Dict[str, Any]:
        dev = self.resolve_target(device_id)
        if not dev:
            return {"status": "error", "message": f"Device '{device_id}' not found in registry."}
        if not dev.is_online or dev.ws is None:
            return {"status": "offline", "message": f"Device '{dev.device_id}' is offline (last seen {dev.last_seen_text()})."}

        t0 = time.time()
        try:
            res = await self.execute_tool_async(
                target_device=dev.device_id,
                tool_name="timedate" if "timedate" in dev.capabilities else "get_timedate",
                arguments={},
                timeout=3.0,
            )
            latency_ms = round((time.time() - t0) * 1000, 1)
            return {
                "status": "online",
                "device_id": dev.device_id,
                "latency_ms": latency_ms,
                "result": res,
            }
        except Exception:
            try:
                t0 = time.time()
                await dev.ws.send_text(json.dumps({"type": "ping", "timestamp": time.time()}))
                dev.last_seen = time.time()
                return {
                    "status": "online",
                    "device_id": dev.device_id,
                    "latency_ms": round((time.time() - t0) * 1000, 1),
                }
            except Exception as e:
                return {"status": "error", "message": f"Ping failed: {e}"}

    def resolve_target(self, target: str, caller_id: Optional[str] = None) -> Optional[Device]:
        """
        Resolves a target descriptor to a Device object.
        Supports:
        - "caller", "current", "me", "this" -> caller_id
        - Exact device_id match
        - Substring in device_id or name
        - Type synonyms: 'phone', 'android', 'pc', 'web', 'browser'
        """
        t = str(target or "caller").strip().lower()

        with self._rw_lock:
            # 1. Caller resolution
            if t in ("caller", "current", "me", "this", "here", "local", ""):
                if caller_id and caller_id.lower() in self._devices:
                    return self._devices[caller_id.lower()]
                online = [d for d in self._devices.values() if d.is_online]
                if online:
                    return online[0]
                if self._devices:
                    return next(iter(self._devices.values()))
                return None

            # 2. Exact match by device_id
            for d_id, dev in self._devices.items():
                if d_id == t:
                    return dev

            # 3. Substring match in device_id
            for d_id, dev in self._devices.items():
                if t in d_id or d_id in t:
                    return dev

            # 4. Match in display name
            for dev in self._devices.values():
                if t in dev.name.lower() or dev.name.lower() in t:
                    return dev

            # 5. Synonyms / Device Type Match
            phone_synonyms = ("phone", "mobile", "android", "cell")
            pc_synonyms = ("pc", "computer", "desktop", "laptop", "workstation", "windows", "linux", "mac", "tui", "terminal", "pc_main")
            web_synonyms = ("web", "browser", "chrome", "edge")

            if t in phone_synonyms:
                for dev in self._devices.values():
                    if dev.device_type.lower() in phone_synonyms or any(s in dev.device_id.lower() for s in phone_synonyms):
                        return dev

            if t in pc_synonyms:
                for dev in self._devices.values():
                    if dev.device_type.lower() in pc_synonyms or any(s in dev.device_id.lower() for s in pc_synonyms):
                        return dev

            if t in web_synonyms:
                for dev in self._devices.values():
                    if dev.device_type.lower() in web_synonyms:
                        return dev

            # 6. Fallback match by device_type string
            for dev in self._devices.values():
                if dev.device_type.lower() == t:
                    return dev

        return None

    def is_online(self, target: str) -> bool:
        """Check whether a target device is currently registered and online."""
        dev = self.resolve_target(target)
        return dev is not None and dev.is_online

    def get_presence_summary(self, caller_id: Optional[str] = None) -> str:
        """
        Compact textual summary of connected and offline devices for the LLM prompt.
        Costs ~30 tokens.
        """
        devices = self.list_devices()
        if not devices:
            return "[DEVICES IN ECOSYSTEM]\n• No external devices connected."

        lines = ["[DEVICES IN ECOSYSTEM]"]
        for d in devices:
            d_id = d["device_id"]
            status = "Online" if d["online"] else f"Offline, last seen {d['last_seen_text']}"
            is_caller = ", Caller" if caller_id and d_id == caller_id.lower() else ""
            tools = ", ".join(d.get("capabilities", [])) or "basic"
            lines.append(f"• {d_id} ({d['name']}, {d['device_type']}, {status}{is_caller}): [{tools}]")

        lines.append(
            "\n[ARCHITECTURE & PC CLIENT GUIDANCE]\n"
            "• AVA Server is a central backend service (may be hosted in the cloud/web).\n"
            "• 'PC' or 'Desktop' refers strictly to the user's PC Client device ('pc_main').\n"
            "• Server task outputs and artifacts reside in the server's central archive, NOT on the user's PC.\n"
            "• To save files or perform actions on the user's PC, invoke `device_tool` targeting 'pc_main' (e.g. tool 'save_file' to save directly to Desktop or Downloads on the user's PC).\n"
            "• If 'pc_main' is offline, never claim the file was saved to the user's PC. State clearly that the PC client is offline, that the file is safely stored in the server archive, and offer to transfer it once the PC connects."
        )
        return "\n".join(lines)

    def handle_rpc_response(self, rpc_id: str, result: Any, error: Optional[str] = None) -> bool:
        """Called when a client WebSocket message returns a tool result."""
        with self._rw_lock:
            fut = self._pending_rpcs.pop(rpc_id, None)
        if fut and not fut.done():
            if error:
                fut.set_exception(RuntimeError(error))
            else:
                fut.set_result(result)
            return True
        return False

    async def send_to_device(self, target_device: str, payload: dict) -> bool:
        """Send a JSON payload directly to a connected device via its active WebSocket."""
        dev = self.resolve_target(target_device)
        if not dev or not dev.is_online or dev.ws is None:
            return False
        try:
            msg_str = json.dumps(payload, ensure_ascii=False)
            await dev.ws.send_text(msg_str)
            dev.last_seen = time.time()
            return True
        except Exception as e:
            logger.warning(f"Failed to send to device '{dev.device_id}': {e}")
            with self._rw_lock:
                if dev.ws:
                    self._ws_to_id.pop(dev.ws, None)
                dev.ws = None
            return False

    async def execute_tool_async(
        self,
        target_device: str,
        tool_name: str,
        arguments: Optional[Dict[str, Any]] = None,
        caller_id: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Asynchronously dispatch a tool call to a target device via its open WebSocket with dynamic fast timeout."""
        if timeout is None:
            t_low = str(tool_name or "").lower()
            if any(k in t_low for k in ("time", "clock", "date")):
                timeout = 1.8
            elif any(k in t_low for k in ("battery", "power")):
                timeout = 2.0
            elif any(k in t_low for k in ("ring", "alarm", "music")):
                timeout = 2.5
            elif any(k in t_low for k in ("location", "gps", "geo")):
                timeout = 4.5
            elif any(k in t_low for k in ("task", "coding", "agent", "computer")):
                timeout = 15.0
            else:
                timeout = 5.0

        dev = self.resolve_target(target_device, caller_id=caller_id)
        if not dev:
            return {
                "status": "error",
                "error": f"Target device '{target_device}' not found in registry.",
                "known_devices": [d["device_id"] for d in self.list_devices()],
            }

        if not dev.is_online or dev.ws is None:
            return {
                "status": "error",
                "error": f"Device '{dev.device_id}' ('{dev.name}') is currently offline (last seen {dev.last_seen_text()}).",
            }

        rpc_id = f"rpc_{uuid.uuid4().hex[:8]}"
        payload = {
            "type": "tool_call",
            "rpc_id": rpc_id,
            "tool": tool_name,
            "arguments": arguments or {},
            "caller_id": caller_id,
            "timestamp": time.time(),
        }

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        with self._rw_lock:
            self._pending_rpcs[rpc_id] = future

        try:
            # Send message down client WebSocket
            msg_str = json.dumps(payload, ensure_ascii=False)
            await dev.ws.send_text(msg_str)
            dev.last_seen = time.time()

            result = await asyncio.wait_for(future, timeout=timeout)
            return {
                "status": "success",
                "device_id": dev.device_id,
                "device_name": dev.name,
                "tool": tool_name,
                "result": result,
            }
        except asyncio.TimeoutError:
            with self._rw_lock:
                self._pending_rpcs.pop(rpc_id, None)
            return {
                "status": "error",
                "error": f"Device '{dev.device_id}' timed out after {timeout}s while executing '{tool_name}'.",
            }
        except Exception as e:
            with self._rw_lock:
                self._pending_rpcs.pop(rpc_id, None)
            return {
                "status": "error",
                "error": f"Failed to execute '{tool_name}' on device '{dev.device_id}': {str(e)}",
            }

    def execute_tool_sync(
        self,
        target_device: str,
        tool_name: str,
        arguments: Optional[Dict[str, Any]] = None,
        caller_id: Optional[str] = None,
        timeout: float = 12.0,
    ) -> Dict[str, Any]:
        """Synchronous wrapper for thread-based handlers."""
        if self._event_loop and self._event_loop.is_running():
            coro = self.execute_tool_async(
                target_device=target_device,
                tool_name=tool_name,
                arguments=arguments,
                caller_id=caller_id,
                timeout=timeout,
            )
            fut = asyncio.run_coroutine_threadsafe(coro, self._event_loop)
            try:
                return fut.result(timeout=timeout + 1.0)
            except Exception as e:
                return {"status": "error", "error": f"Execution error: {e}"}
        else:
            try:
                return asyncio.run(
                    self.execute_tool_async(
                        target_device=target_device,
                        tool_name=tool_name,
                        arguments=arguments,
                        caller_id=caller_id,
                        timeout=timeout,
                    )
                )
            except Exception as e:
                return {"status": "error", "error": f"Asyncio error: {e}"}

    def search_tools(self, query: str, target_device: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        OpenAI-style tool search: finds tools matching a keyword or intent
        across registered devices.
        """
        q = str(query).lower().strip()
        results: List[Dict[str, Any]] = []

        with self._rw_lock:
            devices = list(self._devices.values())

        if target_device:
            resolved = self.resolve_target(target_device)
            devices = [resolved] if resolved else []

        for dev in devices:
            for tool_name, info in dev.tool_catalog.items():
                if isinstance(info, dict):
                    desc = info.get("description", "")
                    params = info.get("parameters", {})
                elif isinstance(info, str):
                    desc = info
                    params = {}
                else:
                    desc = str(info)
                    params = {}

                if q in tool_name.lower() or q in desc.lower():
                    results.append({
                        "device_id": dev.device_id,
                        "device_name": dev.name,
                        "online": dev.is_online,
                        "tool": tool_name,
                        "description": desc,
                        "parameters": params,
                    })

            # Check general capabilities list if not explicitly in catalog
            for cap in dev.capabilities:
                if q in cap.lower() and not any(r["tool"] == cap and r["device_id"] == dev.device_id for r in results):
                    results.append({
                        "device_id": dev.device_id,
                        "device_name": dev.name,
                        "online": dev.is_online,
                        "tool": cap,
                        "description": f"Device capability '{cap}' provided by {dev.name}",
                        "parameters": {},
                    })

        return results


# Global singleton
device_registry = DeviceRegistry()
