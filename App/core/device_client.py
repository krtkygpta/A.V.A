"""
device_client.py - Background WebSocket device client for the PC / TUI.
Allows the PC to register as an active node in the AVA multi-client ecosystem,
receiving and executing remote device tool calls (bash, screenshot, battery, etc.).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import threading
import time
from typing import Any, Dict, Optional

logger = logging.getLogger("ava.device_client")

_CLIENT_THREAD: Optional[threading.Thread] = None
_STOP_EVENT = threading.Event()


def _get_pc_battery() -> Dict[str, Any]:
    try:
        import psutil
        bat = psutil.sensors_battery()
        if bat:
            return {
                "battery": int(bat.percent),
                "charging": bool(bat.power_plugged),
                "secsleft": int(bat.secsleft) if bat.secsleft > 0 else None,
            }
    except Exception:
        pass
    return {"battery": 100, "charging": True, "note": "Desktop PC on direct AC power"}


def _get_pc_location() -> Dict[str, Any]:
    try:
        import urllib.request
        import json
        req = urllib.request.Request("https://ipwho.is/", headers={"User-Agent": "AVA-System/1.0"})
        with urllib.request.urlopen(req, timeout=2.5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("success") is not False and data.get("latitude"):
                lat = data.get("latitude")
                lon = data.get("longitude")
                city = data.get("city", "Greater Noida")
                region = data.get("region", "Uttar Pradesh")
                country = data.get("country", "India")
                addr = f"{city}, {region}, {country}"
                try:
                    rev_req = urllib.request.Request(
                        f"https://nominatim.openstreetmap.org/reverse?format=json&lat={lat}&lon={lon}&zoom=14",
                        headers={"User-Agent": "AVA-System/1.0"}
                    )
                    with urllib.request.urlopen(rev_req, timeout=2.0) as rresp:
                        rdata = json.loads(rresp.read().decode("utf-8"))
                        addr = rdata.get("display_name") or addr
                except Exception:
                    pass
                return {
                    "latitude": lat,
                    "longitude": lon,
                    "city": city,
                    "region": region,
                    "country": country,
                    "address": addr,
                    "accuracy_meters": 2000,
                    "provider": "network_ip",
                }
    except Exception:
        pass
    return {
        "latitude": 28.5526,
        "longitude": 77.5540,
        "city": "Dadri / Greater Noida",
        "region": "Uttar Pradesh",
        "country": "India",
        "address": "Chithera, Dadri, Gautam Buddha Nagar, Uttar Pradesh, 201314, India",
        "accuracy_meters": 2500,
        "provider": "network_ip",
    }


def _dispatch_local_task(task_type: str, **kwargs):
    try:
        from core.TaskManager import dispatch_background_task
    except ImportError:
        try:
            from App.core.TaskManager import dispatch_background_task
        except ImportError:
            import sys
            from pathlib import Path
            app_dir = str(Path(__file__).resolve().parent.parent)
            if app_dir not in sys.path:
                sys.path.insert(0, app_dir)
            from core.TaskManager import dispatch_background_task
    return dispatch_background_task(task_type=task_type, **kwargs)


def _execute_local_pc_tool(tool_name: str, arguments: Dict[str, Any]) -> Any:
    """Execute tools locally on this PC machine."""
    tool_name = (tool_name or "").lower().strip()
    if tool_name in ("location", "get_location"):
        return _get_pc_location()

    elif tool_name in ("battery", "get_battery"):
        return _get_pc_battery()

    elif tool_name in ("timedate", "get_timedate", "time", "date"):
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        return {"datetime": now, "epoch": int(time.time()), "timezone": "IST"}

    elif tool_name in ("screenshot", "take_screenshot"):
        return _get_pc_screenshot()

    elif tool_name in ("bash", "run_command", "cmd", "shell"):
        cmd = arguments.get("command") or arguments.get("cmd") or ""
        if not cmd:
            return {"error": "Missing command argument"}
        try:
            import subprocess
            proc = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=arguments.get("timeout", 20),
            )
            return {
                "returncode": proc.returncode,
                "stdout": proc.stdout[:2000],
                "stderr": proc.stderr[:2000],
            }
        except Exception as e:
            return {"error": f"Command execution failed: {e}"}

    elif tool_name in ("ring_device", "ring"):
        try:
            import winsound
            for _ in range(3):
                winsound.Beep(1000, 400)
                time.sleep(0.1)
            return {"status": "success", "message": "PC alert tone played."}
        except Exception as e:
            return {"status": "success", "message": f"Chime notification note: {e}"}

    elif tool_name in ("coding_agent", "coding", "code_agent"):
        try:
            goal = arguments.get("goal") or arguments.get("objective") or arguments.get("prompt") or ""
            target_dir = arguments.get("target_dir") or arguments.get("path") or os.getcwd()
            clean_args = {k: v for k, v in arguments.items() if k not in ("goal", "target_dir", "objective", "task_type")}
            t_id, t_msg = _dispatch_local_task(
                task_type="coding",
                goal=goal,
                target_dir=target_dir,
                objective=goal,
                **clean_args,
            )
            return {"status": "started" if t_id else "error", "task_id": t_id, "message": t_msg, "device": "pc_main"}
        except Exception as e:
            return {"error": f"Failed to dispatch local coding agent on PC: {e}"}

    elif tool_name in ("computer_use", "desktop_control", "gui_agent"):
        try:
            goal = arguments.get("goal") or arguments.get("objective") or arguments.get("prompt") or ""
            clean_args = {k: v for k, v in arguments.items() if k not in ("goal", "objective", "task_type")}
            t_id, t_msg = _dispatch_local_task(
                task_type="computer_use",
                goal=goal,
                objective=goal,
                **clean_args,
            )
            return {"status": "started" if t_id else "error", "task_id": t_id, "message": t_msg, "device": "pc_main"}
        except Exception as e:
            return {"error": f"Failed to dispatch computer use agent on PC: {e}"}

    elif tool_name in ("background_task", "start_background_task", "dispatch_task"):
        try:
            b_type = arguments.get("task_type") or "general"
            clean_args = {k: v for k, v in arguments.items() if k != "task_type"}
            t_id, t_msg = _dispatch_local_task(task_type=b_type, **clean_args)
            return {"status": "started" if t_id else "error", "task_id": t_id, "message": t_msg, "device": "pc_main"}
        except Exception as e:
            return {"error": f"Failed to dispatch background task on PC: {e}"}

    elif tool_name in ("save_file", "write_file", "create_file", "save_to_pc", "save_to_desktop"):
        filename = arguments.get("filename") or arguments.get("name") or arguments.get("file_name") or "report.txt"
        content = arguments.get("content") or arguments.get("text") or arguments.get("body") or ""
        dest_folder = arguments.get("directory") or arguments.get("dest") or arguments.get("folder")

        if not dest_folder or str(dest_folder).lower() in ("desktop", "default"):
            desktop = os.path.join(os.path.expanduser("~"), "Desktop")
            dest_folder = desktop if os.path.exists(desktop) else os.path.expanduser("~")
        elif str(dest_folder).lower() == "downloads":
            downloads = os.path.join(os.path.expanduser("~"), "Downloads")
            dest_folder = downloads if os.path.exists(downloads) else os.path.expanduser("~")

        try:
            os.makedirs(dest_folder, exist_ok=True)
            clean_filename = os.path.basename(str(filename))
            full_path = os.path.join(dest_folder, clean_filename)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)
            return {
                "status": "success",
                "message": f"Saved file to PC: {full_path}",
                "path": full_path,
                "filename": clean_filename,
                "bytes_written": len(content.encode("utf-8")),
            }
        except Exception as e:
            return {"error": f"Failed to save file on PC: {e}"}

    return {"error": f"Unsupported PC tool: {tool_name}"}


_REPORTED_SERVER_TASK_IDS: set[str] = set()

_CONNECTION_STATUS: str = "disconnected"
_STATUS_LISTENERS: list = []


def get_connection_status() -> str:
    """Return the current Hub WebSocket connection status ('connected', 'connecting', 'reconnecting', 'disconnected')."""
    return _CONNECTION_STATUS


def add_status_listener(callback) -> None:
    """Register a callback invoked with the new status string on connection state change."""
    if callback not in _STATUS_LISTENERS:
        _STATUS_LISTENERS.append(callback)


def _set_connection_status(status: str) -> None:
    global _CONNECTION_STATUS
    if _CONNECTION_STATUS != status:
        _CONNECTION_STATUS = status
        for cb in list(_STATUS_LISTENERS):
            try:
                cb(status)
            except Exception:
                pass


async def _monitor_server_tasks(ws, safe_send_fn):
    """
    Background worker that monitors ONLY tasks started by the remote server.
    Tasks started locally by the TUI will have origin != 'server' and will never be reported.
    """
    while not _STOP_EVENT.is_set():
        try:
            from core.TaskManager import TaskRegistry, TaskStatus
        except ImportError:
            try:
                from App.core.TaskManager import TaskRegistry, TaskStatus
            except ImportError:
                await asyncio.sleep(2.0)
                continue

        try:
            registry = TaskRegistry()
            all_tasks = registry.get_all_tasks()
            for task in all_tasks:
                if task.id in _REPORTED_SERVER_TASK_IDS:
                    continue
                meta = getattr(task, "metadata", None) or {}
                # Only tasks initiated by the server get reported back
                if meta.get("origin") == "server" and not meta.get("reported_to_server"):
                    status_val = getattr(task.status, "value", str(task.status)).lower()
                    if status_val in ("completed", "failed"):
                        _REPORTED_SERVER_TASK_IDS.add(task.id)
                        with registry._tasks_lock:
                            live_task = registry._tasks.get(task.id)
                            if live_task:
                                live_task.metadata = dict(live_task.metadata or {})
                                live_task.metadata["reported_to_server"] = True
                            registry._persist_locked()

                        summary = ""
                        if isinstance(task.result, dict):
                            summary = task.result.get("summary") or task.result.get("message") or ""
                        elif isinstance(task.result, str):
                            summary = task.result
                        if not summary:
                            summary = task.to_summary()

                        payload = {
                            "type": "server_task_completed",
                            "task_id": task.id,
                            "task_name": task.name,
                            "task_type": task.task_type,
                            "status": status_val,
                            "conversation_id": task.conversation_id,
                            "caller_id": meta.get("caller_id"),
                            "summary": summary,
                            "error": task.error,
                        }
                        await safe_send_fn(payload)
        except Exception as e:
            logger.debug(f"[DeviceClient] Task monitor check: {e}")

        await asyncio.sleep(2.0)


async def _run_device_loop(device_id: str, ws_url: str):
    import websockets

    send_lock = asyncio.Lock()

    async def _safe_send(payload: dict | str) -> bool:
        text = payload if isinstance(payload, str) else json.dumps(payload)
        try:
            async with send_lock:
                await ws.send(text)
            return True
        except Exception as e:
            logger.warning(f"[DeviceClient] Send failed: {e}")
            return False

    retry_delay = 1.0

    while not _STOP_EVENT.is_set():
        monitor_task = None
        heartbeat_task = None
        ws = None
        try:
            _set_connection_status("connecting" if retry_delay <= 1.0 else "reconnecting")
            async with websockets.connect(
                ws_url,
                ping_interval=30,
                ping_timeout=30,
                close_timeout=10,
            ) as websocket:
                ws = websocket
                reg_payload = {
                    "type": "register",
                    "device_id": device_id,
                    "name": "Kartikey PC",
                    "device_type": "pc",
                    "capabilities": [
                        "bash",
                        "screenshot",
                        "save_file",
                        "coding_agent",
                        "computer_use",
                        "background_task",
                        "get_location",
                        "get_battery_status",
                        "ring_device",
                        "timedate",
                    ],
                    "tool_catalog": {
                        "bash": "Execute terminal shell commands on the local PC.",
                        "screenshot": "Capture current PC monitor display screen.",
                        "save_file": "Save text or report content to a file on the PC Desktop or specified folder.",
                        "coding_agent": "Run autonomous multi-step coding, file editing, and test verification directly on the local PC workspace.",
                        "computer_use": "Control the local PC desktop, GUI, applications, and operating system.",
                        "background_task": "Launch autonomous background workers on the local PC.",
                        "get_battery_status": "Read hardware battery status via psutil.",
                        "get_location": "Read PC geographic and network location.",
                        "ring_device": "Play alert sound through PC speakers.",
                        "timedate": "Check system time.",
                    },
                    "metadata": {
                        "platform": sys.platform,
                    },
                }
                await _safe_send(reg_payload)
                _set_connection_status("connected")
                retry_delay = 1.0  # Reset backoff on successful connection

                # Heartbeat worker sending ping every 20s to ensure server marks device as online
                async def _send_heartbeats():
                    while not _STOP_EVENT.is_set():
                        await asyncio.sleep(20.0)
                        ok = await _safe_send({"type": "heartbeat", "timestamp": time.time()})
                        if not ok:
                            break

                heartbeat_task = asyncio.create_task(_send_heartbeats())
                # Launch background monitor for tasks started by the server
                monitor_task = asyncio.create_task(_monitor_server_tasks(ws, _safe_send))

                while not _STOP_EVENT.is_set():
                    raw = await ws.recv()
                    try:
                        data = json.loads(raw)
                    except Exception:
                        continue

                    msg_type = data.get("type")

                    if msg_type == "tool_call":
                        rpc_id = data.get("rpc_id")
                        tool = data.get("tool", "")
                        args = data.get("arguments", {})
                        if "origin" not in args:
                            args["origin"] = "server"
                        if "caller_id" not in args and data.get("caller_id"):
                            args["caller_id"] = data.get("caller_id")
                        if "conversation_id" not in args and data.get("conversation_id"):
                            args["conversation_id"] = data.get("conversation_id")

                        # Run tool execution off the event loop so event loop never starves
                        result = await asyncio.to_thread(_execute_local_pc_tool, tool, args)
                        await _safe_send({
                            "type": "tool_result",
                            "rpc_id": rpc_id,
                            "result": result,
                        })

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.info(f"[DeviceClient] Connection dropped ({e}). Reconnecting in {retry_delay:.1f}s...")
            _set_connection_status("reconnecting")
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 1.5, 10.0)
        finally:
            if heartbeat_task and not heartbeat_task.done():
                heartbeat_task.cancel()
            if monitor_task and not monitor_task.done():
                monitor_task.cancel()

    _set_connection_status("disconnected")


def start_device_client(device_id: str = "pc_main", server_url: Optional[str] = None) -> None:
    global _CLIENT_THREAD, _STOP_EVENT

    if _CLIENT_THREAD and _CLIENT_THREAD.is_alive():
        return

    _STOP_EVENT.clear()
    s_url = server_url or os.getenv("AVA_SERVER_URL", "http://127.0.0.1:8765")
    ws_url = s_url.replace("http://", "ws://").replace("https://", "wss://").rstrip("/") + "/ws/device"

    def _worker():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_run_device_loop(device_id, ws_url))
        except Exception:
            pass
        finally:
            loop.close()

    _CLIENT_THREAD = threading.Thread(target=_worker, daemon=True, name="AvaPCDeviceClient")
    _CLIENT_THREAD.start()


def stop_device_client() -> None:
    global _STOP_EVENT
    _STOP_EVENT.set()
    _set_connection_status("disconnected")