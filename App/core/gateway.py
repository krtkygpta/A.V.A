"""
gateway.py - Remote Gateway & WebSocket Server for AVA Mobile and Web Clients.

Provides:
1. Fast, async REST API for status, chat history, live tasks, and command execution.
2. Real-time WebSocket event streaming (/ws) for 1:1 parity with TUI.
3. Static file server hosting the Web Portal.
4. EventBroadcaster singleton allowing any AVA component to push updates.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
import uvicorn

# Suppress noisy access logs
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

GATEWAY_HOST = os.getenv("AVA_GATEWAY_HOST", "0.0.0.0")
GATEWAY_PORT = int(os.getenv("AVA_GATEWAY_PORT", "8766"))
WEB_DIR = Path(__file__).resolve().parent.parent / "ui" / "web"
UPLOADS_DIR = Path(__file__).resolve().parent.parent / "data" / "uploads"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


# ── Event Broadcaster Singleton ───────────────────────────────────────────────

class EventBroadcaster:
    """Manages connected WebSocket clients and dispatches real-time events."""

    _instance: Optional["EventBroadcaster"] = None
    _lock = threading.Lock()

    def __new__(cls) -> "EventBroadcaster":
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._connections = set()
                cls._instance._loop = None
                cls._instance._conn_lock = threading.Lock()
            return cls._instance

    def set_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def add_connection(self, ws: WebSocket) -> None:
        with self._conn_lock:
            self._connections.add(ws)

    def remove_connection(self, ws: WebSocket) -> None:
        with self._conn_lock:
            self._connections.discard(ws)

    def broadcast_sync(self, event_type: str, data: Dict[str, Any]) -> None:
        """Thread-safe event broadcast to all active WebSocket clients."""
        payload = {
            "type": event_type,
            "data": data,
            "timestamp": datetime.now().isoformat(),
        }
        json_str = json.dumps(payload, ensure_ascii=False)

        with self._conn_lock:
            active_sockets = list(self._connections)

        if not active_sockets or self._loop is None:
            return

        async def _send_all():
            dead_sockets = []
            for ws in active_sockets:
                try:
                    await ws.send_text(json_str)
                except Exception:
                    dead_sockets.append(ws)
            if dead_sockets:
                with self._conn_lock:
                    for ws in dead_sockets:
                        self._connections.discard(ws)

        try:
            asyncio.run_coroutine_threadsafe(_send_all(), self._loop)
        except Exception:
            pass


def get_broadcaster() -> EventBroadcaster:
    return EventBroadcaster()


_last_broadcast_user_msg = None
_last_broadcast_assistant_msg = None


# ── Convenience broadcast helpers ─────────────────────────────────────────────

def broadcast_user_message(text: str, user: str = "User", attachments: Optional[List[Dict[str, Any]]] = None) -> None:
    global _last_broadcast_user_msg
    clean = (text or "").strip()
    key = (clean, json.dumps(attachments or [], sort_keys=True))
    if not clean and not attachments:
        return
    if _last_broadcast_user_msg == key:
        return
    _last_broadcast_user_msg = key

    get_broadcaster().broadcast_sync(
        "msg:user",
        {
            "user": user,
            "text": clean,
            "attachments": attachments or [],
            "time": datetime.now().strftime("%H:%M"),
        },
    )


def broadcast_assistant_message(text: str, assistant: str = "AVA") -> None:
    global _last_broadcast_assistant_msg
    clean = (text or "").strip()
    if not clean:
        return
    if _last_broadcast_assistant_msg == clean:
        return
    _last_broadcast_assistant_msg = clean

    get_broadcaster().broadcast_sync(
        "msg:assistant",
        {
            "assistant": assistant,
            "text": clean,
            "time": datetime.now().strftime("%H:%M"),
        },
    )


def broadcast_tool_start(tool_name: str, args: Any, tool_id: str = "") -> None:
    get_broadcaster().broadcast_sync(
        "tool:start",
        {
            "tool_id": tool_id or f"tool_{int(time.time() * 1000)}",
            "name": tool_name,
            "args": args,
            "time": datetime.now().strftime("%H:%M"),
        },
    )


def broadcast_tool_result(tool_name: str, result: str, tool_id: str = "", error: bool = False) -> None:
    get_broadcaster().broadcast_sync(
        "tool:result",
        {
            "tool_id": tool_id,
            "name": tool_name,
            "result": str(result),
            "error": error,
            "time": datetime.now().strftime("%H:%M"),
        },
    )


def broadcast_status(status: str, processing: bool = False) -> None:
    get_broadcaster().broadcast_sync(
        "status:change",
        {
            "status": status,
            "processing": processing,
        },
    )


def broadcast_notification(title: str, message: str, priority: str = "normal", sound: bool = True) -> None:
    get_broadcaster().broadcast_sync(
        "event:notification",
        {
            "title": str(title),
            "message": str(message),
            "priority": priority,
            "sound": sound,
            "time": datetime.now().strftime("%H:%M"),
        },
    )


def broadcast_system_message(text: str, priority: str = "normal") -> None:
    get_broadcaster().broadcast_sync(
        "msg:system",
        {
            "text": text,
            "priority": priority,
            "time": datetime.now().strftime("%H:%M"),
        },
    )


def broadcast_tasks_sync() -> None:
    """Pushes fresh state of all running background tasks to connected clients."""
    tasks = get_all_tasks_payload()
    get_broadcaster().broadcast_sync("tasks:sync", {"tasks": tasks})


# ── Task & State Query Helpers ────────────────────────────────────────────────

def get_all_tasks_payload() -> List[Dict[str, Any]]:
    """Gathers running, pending, and recent background tasks with metadata."""
    payload = []
    try:
        from core.TaskManager import TaskRegistry

        registry = TaskRegistry()
        with registry._tasks_lock:
            for tid, task in registry._tasks.items():
                now = datetime.now()
                meta = getattr(task, "metadata", {}) or {}
                duration_s = getattr(task, "duration", None)
                elapsed_s = (now - task.created_at).total_seconds()
                
                remaining_s = None
                if duration_s and task.status.value in ("running", "pending"):
                    remaining_s = max(0, int(duration_s - elapsed_s))

                payload.append({
                    "id": str(task.id),
                    "name": getattr(task, "name", "") or getattr(task, "task_type", "") or tid,
                    "task_type": getattr(task, "task_type", "general"),
                    "status": task.status.value,
                    "kind": getattr(task, "kind", "short"),
                    "alert_mode": getattr(task, "alert_mode", "silent"),
                    "created_at": task.created_at.strftime("%H:%M:%S"),
                    "duration_seconds": duration_s,
                    "remaining_seconds": remaining_s,
                    "progress": getattr(task, "progress", 0.0),
                    "summary": str(getattr(task, "result_summary", "") or getattr(task, "description", "") or ""),
                    "is_running": task.status.value in ("running", "pending"),
                    "session_id": meta.get("session_id") or meta.get("delegate_session_id") or "",
                    "specialist": meta.get("specialist") or "",
                    "current_activity": meta.get("current_activity") or "",
                    "watchable": getattr(task, "task_type", "") in ("delegate", "opencode", "mission", "research") or bool(meta.get("session_id")) or bool(meta.get("messages")),
                })
    except Exception as e:
        print(f"[Gateway] get_all_tasks_payload error: {e}")
    
    # Sort running tasks first, then by creation date desc
    payload.sort(key=lambda t: (not t["is_running"], t["created_at"]), reverse=False)
    return payload


def get_conversation_history_payload() -> List[Dict[str, Any]]:
    """Retrieves full conversation messages for initial synchronization."""
    out = []
    tool_call_name_map = {}
    tool_call_args_map = {}
    try:
        from core.messageHandler import messages, messages_lock
        from config import USER_NAME

        raw_list = []
        with messages_lock:
            raw_list = list(messages)

        # Check if raw_list contains any actual user/assistant/tool messages
        has_dialogue = any(
            isinstance(m, dict) and m.get("role") in ("user", "assistant", "tool")
            for m in raw_list
        )

        # Fallback to current conversation in ConversationManager if live memory has no dialogue
        if not has_dialogue:
            try:
                from knowledge.ConversationManager import get_manager
                mgr = get_manager()
                conv = mgr.get_current_conversation() or mgr.ensure_current_conversation()
                if conv and conv.messages:
                    raw_list = list(conv.messages)
            except Exception:
                pass

        for msg in raw_list:
            if not isinstance(msg, dict):
                continue
            role = msg.get("role")
            if role == "system":
                # Only include relevant status/task injects (and never the main system prompt)
                content = str(msg.get("content") or "")
                if content.startswith("[BACKGROUND TASK DONE]") or content.startswith("[IMMEDIATE]") or content.startswith("[TASK NEEDS INPUT]"):
                    out.append({"role": "system", "content": content, "timestamp": ""})
            elif role in ("user", "assistant"):
                tool_calls = msg.get("tool_calls") or []
                for tc in tool_calls:
                    if isinstance(tc, dict):
                        t_id = tc.get("id")
                        f_info = tc.get("function") or {}
                        f_name = f_info.get("name")
                        f_args = f_info.get("arguments", "{}")
                    else:
                        t_id = getattr(tc, "id", None)
                        f_info = getattr(tc, "function", None)
                        f_name = getattr(f_info, "name", None)
                        f_args = getattr(f_info, "arguments", "{}")
                    if t_id and f_name:
                        tool_call_name_map[t_id] = f_name
                        try:
                            if isinstance(f_args, str):
                                tool_call_args_map[t_id] = json.loads(f_args) if f_args.strip() else {}
                            else:
                                tool_call_args_map[t_id] = f_args
                        except Exception:
                            tool_call_args_map[t_id] = f_args

                raw_content = msg.get("content")
                content_str = str(raw_content or "") if not isinstance(raw_content, list) else "..."
                if role == "user" and content_str.startswith(f"{USER_NAME}: "):
                    content_str = content_str[len(USER_NAME) + 2:]

                msg_attachments = msg.get("attachments") or []
                if msg_attachments and role == "user":
                    attachment_paths = set()
                    for att in msg_attachments:
                        if isinstance(att, dict):
                            p = att.get("path")
                            if p:
                                attachment_paths.add(str(p).strip().lower().replace("/", "\\"))
                                attachment_paths.add(str(p).strip().lower().replace("\\", "/"))

                    clean_lines = []
                    for line in content_str.split("\n"):
                        line_s = line.strip()
                        norm_line = line_s.lower().replace("/", "\\")
                        norm_line_f = line_s.lower().replace("\\", "/")
                        if (
                            norm_line in attachment_paths
                            or norm_line_f in attachment_paths
                            or line_s.startswith("[Attached Document:")
                            or ((":" in line_s or "/" in line_s or "\\" in line_s) and any(ext in line_s.lower() for ext in (".jpg", ".png", ".jpeg", ".webp", ".pdf", ".txt", ".gif", ".mp3", ".wav", ".json", ".csv")))
                        ):
                            continue
                        clean_lines.append(line)
                    content_str = "\n".join(clean_lines).strip()

                if content_str.strip() or msg_attachments or not tool_calls:
                    out.append({
                        "role": role,
                        "content": content_str,
                        "attachments": msg_attachments,
                        "tool_calls": msg.get("tool_calls"),
                        "timestamp": msg.get("timestamp", ""),
                    })
            elif role == "tool":
                t_id = msg.get("tool_call_id")
                raw_content = msg.get("content")
                out.append({
                    "role": "tool",
                    "name": msg.get("name") or tool_call_name_map.get(t_id, "tool"),
                    "args": msg.get("args") or tool_call_args_map.get(t_id, {}),
                    "content": str(raw_content or "") if not isinstance(raw_content, list) else "tool result",
                    "tool_call_id": t_id,
                    "timestamp": msg.get("timestamp", ""),
                })
    except Exception as e:
        print(f"[Gateway] get_conversation_history_payload error: {e}")
    return out


# ── FastAPI Application Setup ─────────────────────────────────────────────────

app = FastAPI(title="AVA Gateway API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class SendTextRequest(BaseModel):
    text: str = ""
    attachments: Optional[List[Dict[str, Any]]] = None


class ActionRequest(BaseModel):
    action: str
    params: Optional[Dict[str, Any]] = None


@app.get("/api/status")
async def api_status():
    """Returns general server & assistant status."""
    from config import ASSISTANT_NAME, USER_NAME
    from core.AppStates import background_task_active
    from core.response_loop import main_running

    return {
        "status": "online",
        "assistant_name": ASSISTANT_NAME,
        "user_name": USER_NAME,
        "is_processing": main_running.is_set(),
        "has_background_tasks": background_task_active.is_set(),
        "gateway_port": GATEWAY_PORT,
        "timestamp": datetime.now().isoformat(),
    }


@app.get("/api/history")
async def api_history():
    """Returns message history of the active conversation."""
    payload = get_conversation_history_payload()
    return {
        "history": payload,
        "messages": payload,
    }


@app.get("/api/tasks")
async def api_tasks():
    """Returns currently running and recent background tasks."""
    return {
        "tasks": get_all_tasks_payload(),
    }


@app.get("/api/download")
async def api_download_file(path: str):
    """Downloads a requested file from the PC to the remote client."""
    from fastapi.responses import FileResponse
    from pathlib import Path
    import mimetypes

    if not path:
        raise HTTPException(status_code=400, detail="Path parameter required")

    clean_path = path.strip().strip("'\"")
    target = Path(clean_path).expanduser().resolve()

    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {target.name}")

    filename = target.name
    media_type, _ = mimetypes.guess_type(str(target))
    if not media_type:
        media_type = "application/octet-stream"

    return FileResponse(
        str(target),
        filename=filename,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


@app.get("/api/tts")
async def api_tts(text: str):
    """Synthesizes text using AVA's original Piper TTS voice and streams WAV audio."""
    from fastapi.responses import Response
    from utils.tts_piper import synthesize_bytes

    if not text or not text.strip():
        raise HTTPException(status_code=400, detail="Text required")

    clean_text = text.strip()
    wav_bytes = synthesize_bytes(clean_text)
    if not wav_bytes:
        raise HTTPException(status_code=500, detail="TTS synthesis failed")

    return Response(
        content=wav_bytes,
        media_type="audio/wav",
        headers={
            "Cache-Control": "no-cache",
            "Content-Length": str(len(wav_bytes))
        }
    )



@app.post("/api/send")
async def api_send_text(req: SendTextRequest):
    """Submits a chat message or slash command into AVA's agent loop."""
    text = (req.text or "").strip()
    attachments = req.attachments or []
    if not text and not attachments:
        raise HTTPException(status_code=400, detail="Text or attachments required")

    # Dispatch asynchronously into AVA
    _dispatch_user_input(text, attachments=attachments)
    return {"status": "dispatched", "text": text, "attachments": attachments}


@app.get("/api/conversations")
async def api_get_conversations(limit: int = 50):
    """Lists saved conversation threads."""
    try:
        from knowledge.ConversationManager import get_manager
        mgr = get_manager()
        return {"conversations": mgr.get_all_conversations(limit=limit)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/conversations/{conversation_id}/select")
async def api_select_conversation(conversation_id: str):
    """Switches active conversation thread to conversation_id."""
    try:
        from knowledge.ConversationManager import get_manager
        mgr = get_manager()
        conv = mgr.switch_to_conversation(conversation_id)
        if not conv:
            raise HTTPException(status_code=404, detail="Conversation not found")
        payload = get_conversation_history_payload()
        get_broadcaster().broadcast_sync("event:conversation_switched", {
            "conversation_id": conversation_id,
            "name": conv.name,
            "history": payload,
        })
        return {"status": "ok", "conversation_id": conversation_id, "name": conv.name, "history": payload}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/conversations/new")
async def api_new_conversation():
    """Triggers saving current conversation and opening a fresh one."""
    try:
        from core.messageHandler import reset_messages
        from knowledge.ConversationManager import save_current_conversation, start_new_conversation

        save_current_conversation()
        start_new_conversation()
        reset_messages()
        get_broadcaster().broadcast_sync("event:new_conversation", {})
        broadcast_system_message("New conversation started")
        return {"status": "ok", "message": "New conversation started"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/tasks/{task_id}/cancel")
async def api_cancel_task(task_id: str):
    """Cancels a running background task."""
    try:
        from core.TaskManager import TaskRegistry, TaskStatus

        registry = TaskRegistry()
        with registry._tasks_lock:
            task = registry._tasks.get(task_id)
            if task:
                task.status = TaskStatus.CANCELLED
                registry._persist_locked()
                broadcast_tasks_sync()
                return {"status": "cancelled", "task_id": task_id}
            else:
                raise HTTPException(status_code=404, detail="Task not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/tasks/{task_id}/watch")
async def api_watch_task(task_id: str):
    """Returns live process, messages, and state for watching a background task."""
    try:
        from core.TaskManager import TaskRegistry
        from core.jobs.delegate_session import get_delegate_session_store

        registry = TaskRegistry()
        task = registry.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        meta = getattr(task, "metadata", {}) or {}
        session_id = meta.get("session_id") or meta.get("delegate_session_id") or ""
        task_type = getattr(task, "task_type", "general")
        specialist = meta.get("specialist") or ""
        current_activity = meta.get("current_activity") or ""
        messages = []

        if session_id:
            store = get_delegate_session_store()
            s_rec = store.get(session_id)
            if s_rec:
                messages = s_rec.messages or []
                if not specialist:
                    specialist = s_rec.specialist
                if not current_activity and s_rec.status == "running":
                    current_activity = "Executing delegated workflow..."

        if not messages and "messages" in meta and isinstance(meta["messages"], list):
            messages = meta["messages"]

        if not messages:
            desc = getattr(task, "description", "")
            if desc:
                messages.append({"role": "user", "content": desc})
            res = getattr(task, "result", "")
            if res:
                messages.append({"role": "assistant", "content": str(res)})

        return {
            "task_id": str(task.id),
            "name": getattr(task, "name", "") or str(task.id),
            "task_type": task_type,
            "status": task.status.value,
            "is_running": task.status.value in ("running", "pending"),
            "session_id": session_id,
            "specialist": specialist,
            "current_activity": current_activity or ("In progress..." if task.status.value in ("running", "pending") else "Completed"),
            "messages": messages,
            "summary": str(getattr(task, "result_summary", "") or meta.get("summary") or getattr(task, "description", "") or ""),
            "created_at": task.created_at.strftime("%H:%M:%S") if hasattr(task, "created_at") else "",
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/upload")
async def api_upload_file(file: UploadFile = File(...)):
    """Uploads any image or document from the Web portal and saves to PC."""
    try:
        filename = file.filename or "upload_file"
        safe_name = re.sub(r'[^a-zA-Z0-9_.-]', '_', filename)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        final_name = f"{stamp}_{safe_name}"
        local_path = UPLOADS_DIR / final_name

        content = await file.read()
        local_path.write_bytes(content)

        ext = local_path.suffix.lower()
        is_image = ext in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".bmp")

        return {
            "status": "success",
            "path": str(local_path.resolve()),
            "filename": filename,
            "stored_name": final_name,
            "url": f"/uploads/{final_name}",
            "is_image": is_image,
            "size": len(content),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/upload_image")
async def api_upload_image(payload: dict[str, Any]):
    """Uploads an image from the Web/Mobile UI and returns its stored server path."""
    try:
        image_base64 = str(payload.get("image_base64", "")).strip()
        filename = payload.get("filename")
        mime = payload.get("mime")
        if not image_base64:
            raise HTTPException(status_code=400, detail="Missing image_base64")

        from core.server_api import store_image_remote
        try:
            result = store_image_remote(
                image_base64=image_base64,
                filename=str(filename) if filename else None,
                mime=str(mime) if mime else None,
            )
            if isinstance(result, dict) and result.get("status") == "success":
                return result
        except Exception:
            pass

        import base64
        ext = ".png"
        if mime and ("jpeg" in mime.lower() or "jpg" in mime.lower()):
            ext = ".jpg"
        elif mime and "svg" in mime.lower():
            ext = ".svg"
        elif mime and "webp" in mime.lower():
            ext = ".webp"

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        name = filename or f"upload_{stamp}{ext}"
        local_path = UPLOADS_DIR / name
        raw_b64 = image_base64.split(",", 1)[-1]
        local_path.write_bytes(base64.b64decode(raw_b64))
        return {
            "status": "success",
            "path": str(local_path.resolve()),
            "filename": name,
            "url": f"/uploads/{name}",
            "is_image": True,
            "size": len(raw_b64),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Real-time bi-directional stream for mobile/web apps."""
    await websocket.accept()
    broadcaster = get_broadcaster()
    broadcaster.add_connection(websocket)

    try:
        from config import ASSISTANT_NAME, USER_NAME
        from knowledge.ConversationManager import get_manager
        mgr = get_manager()
        # Send initial handshake state
        initial_state = {
            "type": "init",
            "data": {
                "assistant_name": ASSISTANT_NAME,
                "user_name": USER_NAME,
                "history": get_conversation_history_payload(),
                "conversations": mgr.get_all_conversations(limit=40),
                "tasks": get_all_tasks_payload(),
            },
            "timestamp": datetime.now().isoformat(),
        }
        await websocket.send_text(json.dumps(initial_state, ensure_ascii=False))

        # Listen for incoming client messages
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
                action = data.get("action") or data.get("type")
                if action in ("send_text", "msg:user"):
                    text = data.get("text") or data.get("content") or ""
                    attachments = data.get("attachments") or []
                    if text.strip() or attachments:
                        _dispatch_user_input(text.strip(), attachments=attachments)
                elif action == "new_conversation":
                    from core.messageHandler import reset_messages, notify_conversation_reset, notify_slash_command
                    from knowledge.ConversationManager import save_current_conversation, start_new_conversation
                    save_current_conversation()
                    start_new_conversation()
                    reset_messages()
                    notify_conversation_reset()
                    notify_slash_command("/new", origin="remote")
                    broadcaster.broadcast_sync("event:new_conversation", {})
                    broadcast_system_message("New conversation started")
                elif action == "get_conversations":
                    convs = mgr.get_all_conversations(limit=40)
                    await websocket.send_text(json.dumps({
                        "type": "conversations:sync",
                        "data": {"conversations": convs},
                        "timestamp": datetime.now().isoformat(),
                    }))
                elif action == "switch_conversation":
                    cid = data.get("conversation_id")
                    if cid:
                        conv = mgr.switch_to_conversation(cid)
                        if conv:
                            payload = get_conversation_history_payload()
                            broadcaster.broadcast_sync("event:conversation_switched", {
                                "conversation_id": cid,
                                "name": conv.name,
                                "history": payload,
                            })
                elif action == "get_tasks":
                    tasks = get_all_tasks_payload()
                    await websocket.send_text(json.dumps({
                        "type": "tasks:sync",
                        "data": {"tasks": tasks},
                        "timestamp": datetime.now().isoformat(),
                    }))
            except json.JSONDecodeError:
                if raw.strip():
                    _dispatch_user_input(raw.strip())
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"[Gateway] WebSocket error: {e}")
    finally:
        broadcaster.remove_connection(websocket)


_last_dispatched_text: str = ""
_last_dispatched_time: float = 0.0

def _dispatch_user_input(text: str, attachments: Optional[List[Dict[str, Any]]] = None) -> None:
    """Injects user input into AVA message pipeline or executes slash commands."""
    global _last_dispatched_text, _last_dispatched_time
    import time
    from config import USER_NAME
    from core.messageHandler import add_message

    clean = text.strip()
    clean_lower = clean.lower()
    now = time.time()

    # Deduplicate exact duplicate user inputs arriving within 2 seconds
    if not attachments and clean and clean == _last_dispatched_text and (now - _last_dispatched_time) < 2.0:
        print(f"[Gateway] Dropping duplicate user input within 2s: {clean}")
        return

    _last_dispatched_text = clean
    _last_dispatched_time = now

    # ── Slash Command Handling ────────────────────────────────────────────────
    if clean.startswith("/"):
        from core.messageHandler import (
            reset_messages,
            notify_conversation_reset,
            notify_clear_chat,
            notify_slash_command,
            notify_system_message,
        )

        notify_slash_command(clean, origin="remote")

        if clean_lower == "/new":
            from knowledge.ConversationManager import save_current_conversation, start_new_conversation
            save_current_conversation()
            start_new_conversation()
            reset_messages()
            notify_conversation_reset()
            get_broadcaster().broadcast_sync("event:new_conversation", {})
            broadcast_system_message("New conversation started")
            return

        if clean_lower == "/clear":
            notify_clear_chat()
            get_broadcaster().broadcast_sync("event:clear_chat", {})
            broadcast_system_message("Chat display cleared")
            return

        if clean_lower == "/tasks":
            broadcast_tasks_sync()
            try:
                from core.TaskManager import get_background_tasks_summary
                summary = get_background_tasks_summary() or "No active background tasks."
                broadcast_system_message(f"Tasks:\n{summary}")
                notify_system_message(f"Tasks:\n{summary}")
            except Exception:
                broadcast_system_message("Tasks synchronized.")
            return

        if clean_lower.startswith("/task "):
            task_id = clean[6:].strip()
            try:
                from core.TaskManager import manage_background_tasks
                result = manage_background_tasks("result", task_id)
                broadcast_system_message(f"Task {task_id}:\n{result}")
                notify_system_message(f"Task {task_id}:\n{result}")
            except Exception as e:
                broadcast_system_message(f"Failed to fetch task {task_id}: {e}")
                notify_system_message(f"Failed to fetch task {task_id}: {e}", is_alert=True)
            return

        if clean_lower.startswith("/mode"):
            mode_arg = clean[5:].strip().lower()
            if mode_arg in ("text", "continuous", "wakeword"):
                notify_system_message(f"Switched mode to {mode_arg}")
                broadcast_system_message(f"Switched mode to {mode_arg}")
            else:
                msg = "Usage: /mode <text|continuous|wakeword>"
                broadcast_system_message(msg)
                notify_system_message(msg)
            return

        if clean_lower.startswith("/theme"):
            theme_arg = clean[6:].strip().lower()
            if theme_arg:
                notify_system_message(f"Theme set to {theme_arg}")
                broadcast_system_message(f"Theme set to {theme_arg}")
            else:
                msg = "Usage: /theme <name> (e.g. /theme oled_monochrome, /theme matrix, /theme dracula)"
                broadcast_system_message(msg)
                notify_system_message(msg)
            return

        if clean_lower in ("/skills", "/plugins"):
            try:
                from core.cc_plugins.skills_host import get_host
                catalog = get_host().catalog_for_main() or "No custom skills installed."
                broadcast_system_message(f"Installed Skills:\n{catalog}")
                notify_system_message(f"Installed Skills:\n{catalog}")
            except Exception as e:
                broadcast_system_message(f"Skills error: {e}")
            return

        if clean_lower in ("/remote", "/qr", "/mobile", "/web"):
            ip = get_local_ip()
            port = int(os.getenv("AVA_GATEWAY_PORT", "8766"))
            url = f"http://{ip}:{port}"
            msg = f"Remote Access URL: {url}\nUse the mobile app camera or browser to pair."
            broadcast_system_message(msg)
            notify_system_message(msg)
            return

        if clean_lower in ("/help", "/commands"):
            help_msg = (
                "Available commands:\n"
                "• /new — Start a fresh conversation\n"
                "• /tasks — Sync & view live background tasks\n"
                "• /task <id> — View full result of a background task\n"
                "• /clear — Clear current chat display\n"
                "• /mode <name> — Switch input mode (text, continuous, wakeword)\n"
                "• /theme <name> — Set active theme\n"
                "• /skills — List installed skills\n"
                "• /remote — Show remote pairing URL\n"
                "• /help — Show this command reference"
            )
            broadcast_system_message(help_msg)
            notify_system_message(help_msg)
            return

        # Generic / unhandled slash command
        broadcast_system_message(f"Command '{clean}' sent to PC terminal.")
        notify_system_message(f"Remote command executed: {clean}")
        return

    # Broadcast echo immediately with attachments so Web and Android see their clean message + photos
    broadcast_user_message(clean, user=USER_NAME, attachments=attachments)
    broadcast_status("thinking...", processing=True)

    # Inject into live LLM pipeline with PC path appended
    if attachments:
        paths_lines = []
        for att in attachments:
            p = att.get("path")
            if p:
                if att.get("is_image"):
                    paths_lines.append(str(p))
                else:
                    paths_lines.append(f"[Attached Document: {p}]")
        paths_str = "\n".join(paths_lines)
        if clean:
            user_prompt = f"{clean}\n{paths_str}"
        else:
            user_prompt = f"Please inspect the attached file(s):\n{paths_str}"
    else:
        user_prompt = clean

    user_msg = f"{USER_NAME}: {user_prompt}"
    add_message(
        role="user",
        content=user_msg,
        tool_id="",
        trigger_response=True,
        broadcast=False,
        attachments=attachments,
    )


# ── Mount Static Web Portal & Uploads ──────────────────────────────────────────

if UPLOADS_DIR.exists():
    app.mount("/uploads", StaticFiles(directory=str(UPLOADS_DIR)), name="uploads")

if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

    @app.get("/")
    async def serve_index():
        index_file = WEB_DIR / "index.html"
        if index_file.exists():
            return FileResponse(str(index_file))
        return JSONResponse({"status": "Web portal files not found in App/ui/web/"})

    @app.get("/manifest.json")
    async def serve_manifest():
        return FileResponse(str(WEB_DIR / "manifest.json"), media_type="application/manifest+json")

    @app.get("/sw.js")
    async def serve_sw():
        return FileResponse(str(WEB_DIR / "sw.js"), media_type="application/javascript")

    @app.get("/icon.svg")
    async def serve_icon():
        return FileResponse(str(WEB_DIR / "icon.svg"), media_type="image/svg+xml")


# ── Gateway Background Server Runner ──────────────────────────────────────────

_server_thread: Optional[threading.Thread] = None
_server_started = threading.Event()


def start_gateway_server(host: str = GATEWAY_HOST, port: int = GATEWAY_PORT) -> bool:
    """Starts the Gateway HTTP + WebSocket server in a background daemon thread."""
    global _server_thread

    if _server_thread and _server_thread.is_alive():
        return True

    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        check_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
        if s.connect_ex((check_host, port)) == 0:
            print(f"[Gateway] Port {port} is already active (shared gateway).")
            return True

    def _run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        get_broadcaster().set_event_loop(loop)

        config = uvicorn.Config(
            app=app,
            host=host,
            port=port,
            loop="asyncio",
            log_level="warning",
            access_log=False,
        )
        server = uvicorn.Server(config)
        _server_started.set()
        loop.run_until_complete(server.serve())

    _server_thread = threading.Thread(target=_run, daemon=True, name="AVAGatewayServer")
    _server_thread.start()

    _server_started.wait(timeout=3.0)
    print(f"[Gateway] Mobile & Web Gateway listening on http://{host}:{port}")
    return True
