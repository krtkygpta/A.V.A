from __future__ import annotations

import asyncio
import atexit
import base64
import datetime
import json
import logging
import os
import sys
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

# Server directory
SERVER_DIR = Path(__file__).resolve().parent
ROOT_DIR = SERVER_DIR.parent
APP_DIR = ROOT_DIR / "App"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))
if str(APP_DIR) not in sys.path:
    sys.path.append(str(APP_DIR))

from config import ROOT_DIR, get_settings, save_settings
from services import ConversationStore, LLMService, ToolService, TTSService, device_registry
from services.blackboard import blackboard
from services.layer1_avatar import layer1_avatar
from services.layer2_orchestrator import layer2_orchestrator
from services.observers.sys_sentinel import SystemSentinel
from services.llm_service import get_server_system_prompt
from services.server_tool_registry import server_tool_registry
from services.token_usage import get_usage_summary

logger = logging.getLogger("AvaServer")

TOOL_SCHEMAS = [
    # --- Server-Side Tools ---
    {
        "type": "function",
        "function": {
            "name": "google_ai",
            "description": "Search the web using Google AI with grounding",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"}
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "view_image",
            "description": "Load and inspect an image from a local server file path or upload into visual context.",
            "parameters": {
                "type": "object",
                "properties": {
                    "image_path": {
                        "type": "string",
                        "description": "Local server file path or relative upload path of the image to inspect (e.g. 'Server/data/uploads/screenshot.png').",
                    },
                    "query": {
                        "type": "string",
                        "description": "Optional specific question or prompt about the image.",
                    },
                },
                "required": ["image_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "image_analysis",
            "description": "Analyze an image using Vision models from base64 data or a local file path",
            "parameters": {
                "type": "object",
                "properties": {
                    "image_base64": {
                        "type": "string",
                        "description": "Base64-encoded image (optional if image_path is provided)",
                    },
                    "image_path": {
                        "type": "string",
                        "description": "File path to the image on the server (e.g. 'Server/data/uploads/image.png')",
                    },
                    "query": {
                        "type": "string",
                        "description": "Question about the image",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "code_execute",
            "description": "Execute Python code in a server-side sandbox",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Python code to execute"},
                    "timeout": {"type": "integer", "description": "Timeout in seconds"},
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "research",
            "description": (
                "Multi-level research agent (1=quick fact check … 5=exhaustive). "
                "Uses web search, page extraction, and text PDF reading under a 100K token budget."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "Research question",
                    },
                    "level": {
                        "type": "integer",
                        "description": "Research depth 1–5 (default 3)",
                        "minimum": 1,
                        "maximum": 5,
                    },
                },
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "store_screenshot",
            "description": (
                "Store a client-uploaded screenshot under Server/data/screenshots "
                "and return the absolute server path."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "image_base64": {
                        "type": "string",
                        "description": "Base64-encoded image (or data URL)",
                    },
                    "filename": {
                        "type": "string",
                        "description": "Optional file name (basename only)",
                    },
                    "mime": {
                        "type": "string",
                        "description": "Optional MIME type, e.g. image/png",
                    },
                },
                "required": ["image_base64"],
            },
        },
    },
    # --- Multi-Client Device Hot Tools ---
    {
        "type": "function",
        "function": {
            "name": "device_location",
            "description": "Get current physical/GPS coordinates and address of target device ('caller' for current device, or specific device name/ID like 'phone', 'pc').",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_device": {
                        "type": "string",
                        "description": "Target device ('caller' or device ID/name like 'phone', 'pc', 'web').",
                    }
                },
                "required": ["target_device"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "device_battery",
            "description": "Check battery percentage and charging status of target device ('caller' or specific device like 'phone', 'laptop').",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_device": {
                        "type": "string",
                        "description": "Target device ('caller' or device ID/name).",
                    }
                },
                "required": ["target_device"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "device_ring",
            "description": "Ring or sound an audible alarm on target device to help find it or get user's attention ('phone', 'pc', 'caller').",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_device": {
                        "type": "string",
                        "description": "Target device to ring (e.g. 'phone', 'pixel_phone').",
                    }
                },
                "required": ["target_device"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "device_music",
            "description": "Control music or audio playback on target device ('play', 'pause', 'stop', 'next', 'volume').",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_device": {
                        "type": "string",
                        "description": "Target device to play on ('caller' or device ID/name).",
                    },
                    "action": {
                        "type": "string",
                        "description": "Media action: 'play', 'pause', 'stop', 'next', 'volume'.",
                    },
                    "query": {
                        "type": "string",
                        "description": "Optional search term or track name to play.",
                    },
                },
                "required": ["target_device", "action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "device_time",
            "description": "Get current local time, date, and timezone from target device ('caller' for current device, or specific device name/ID like 'phone', 'pc').",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_device": {
                        "type": "string",
                        "description": "Target device ('caller' or device ID/name like 'phone', 'pc', 'web').",
                    }
                },
                "required": ["target_device"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "device_tool",
            "description": "Execute a specific tool on a connected client device (e.g. 'save_file' to save files to the PC Client, 'screenshot', 'bash', 'location', 'battery', 'ring', 'music', 'timedate').",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_device": {
                        "type": "string",
                        "description": "Target device ID or 'caller'.",
                    },
                    "tool_name": {
                        "type": "string",
                        "description": "Name of device tool to execute.",
                    },
                    "arguments": {
                        "type": "object",
                        "description": "Arguments for the device tool.",
                    },
                },
                "required": ["target_device", "tool_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tool_search",
            "description": "Search the device and server tool catalog for specialized or custom tools.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What capability or tool to look for.",
                    },
                    "target_device": {
                        "type": "string",
                        "description": "Optional target device ID to filter search.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "conversation_history",
            "description": (
                "Inspect, retrieve, or search past conversation history and transcripts. "
                "Use this tool whenever the user asks about previous chats, 'what did we talk about in the last chat?', "
                "or wants to recall details, topics, code, or specs discussed in any earlier conversation."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": (
                            "Action to perform: "
                            "'get' (retrieve messages from a specific conversation or 'last' chat), "
                            "'list' (show list of recent conversations with their IDs, names, and timestamps), "
                            "'search' (search all conversations for keywords or topics)."
                        ),
                        "enum": ["get", "list", "search"],
                    },
                    "conversation_id": {
                        "type": "string",
                        "description": (
                            "Conversation ID to retrieve. Use 'last', 'previous', or 'latest' to automatically get "
                            "the most recent prior chat. Omit for 'list' or 'search'."
                        ),
                    },
                    "query": {
                        "type": "string",
                        "description": "Keywords or topic to search for when action is 'search'.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of messages or conversations to return (default 20).",
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_manager",
            "description": (
                "Save, retrieve, search, or delete durable memories about the user. "
                "Use 'save' to remember facts/preferences, 'retrieve'/'search' to recall before answering, "
                "'delete' to forget. Call retrieve BEFORE answering questions about the user."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "Action to perform: 'save', 'retrieve', 'delete', or 'categories'.",
                        "enum": ["save", "retrieve", "delete", "categories"],
                    },
                    "category": {
                        "type": "string",
                        "description": "Category hint (favorites, facts, people, etc.).",
                    },
                    "key": {
                        "type": "string",
                        "description": "What the memory is about (save) or search/delete term (retrieve/delete).",
                    },
                    "value": {
                        "type": "string",
                        "description": "The information to remember (required for save).",
                    },
                    "search": {
                        "type": "string",
                        "description": "Search query for retrieve.",
                    },
                },
                "required": ["action"],
            },
        },
    },
    # --- Background Agents & Tasks ---
    {
        "type": "function",
        "function": {
            "name": "background_task",
            "description": (
                "Dispatch a long-running agentic task to execute in the background. "
                "Available agents: 'research' (multi-source web investigation), 'coding' (precision code agent), "
                "'delegate' (specialist AI worker), 'timer' (countdown timer/alert), 'scrape' (web scraper), "
                "'mission' (multi-worker mission), 'demo' (timed test job). "
                "Runs in the background while user continues chatting."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task_type": {
                        "type": "string",
                        "description": "Type of background agent task.",
                        "enum": ["research", "coding", "delegate", "timer", "scrape", "mission", "demo", "opencode"],
                    },
                    "objective": {
                        "type": "string",
                        "description": "Instruction or task objective for coding/delegate/mission.",
                    },
                    "topic": {
                        "type": "string",
                        "description": "Research topic, demo description, or timer label.",
                    },
                    "level": {
                        "type": "integer",
                        "description": "Research depth 1 (quick) to 5 (exhaustive).",
                        "minimum": 1,
                        "maximum": 5,
                    },
                    "duration": {
                        "type": "integer",
                        "description": "Duration in seconds for timer or demo.",
                    },
                    "message": {
                        "type": "string",
                        "description": "Spoken/toast notification message on completion.",
                    },
                    "model_tier": {
                        "type": "string",
                        "description": "Model tier: low, medium_low, medium, high, very_high.",
                        "enum": ["low", "medium_low", "medium", "high", "very_high"],
                    },
                },
                "required": ["task_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_background_tasks_status",
            "description": "Get current status and summary of all running and recent background agent tasks.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "manage_background_tasks",
            "description": "Inspect, list, retrieve results from, cancel, dismiss, or answer background tasks.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "Action: 'list', 'status', 'result', 'cancel', 'dismiss', 'answer', 'context'.",
                        "enum": ["list", "status", "result", "cancel", "dismiss", "answer", "context"],
                    },
                    "task_id": {
                        "type": "string",
                        "description": "Task ID for status/result/cancel/dismiss/answer.",
                    },
                    "answer": {
                        "type": "string",
                        "description": "Answer text for a task awaiting clarification.",
                    },
                },
                "required": ["action"],
            },
        },
    },
]


def get_all_server_tool_schemas() -> list[dict]:
    """Merge static built-in server tools with dynamic plugin and MCP tools."""
    base_schemas = list(TOOL_SCHEMAS)
    existing_names = {s.get("function", {}).get("name") for s in base_schemas}
    try:
        for dynamic_schema in server_tool_registry.get_schemas():
            d_name = dynamic_schema.get("function", {}).get("name")
            if d_name and d_name not in existing_names:
                base_schemas.append(dynamic_schema)
                existing_names.add(d_name)
    except Exception as e:
        logger.warning(f"Error fetching dynamic server tool schemas: {e}")
    return base_schemas


class AvaServer:
    """Core backend engine for LLM generation, TTS, device hub, and conversations."""

    def __init__(self) -> None:
        settings = get_settings()
        self.host = settings.host
        self.port = settings.port

        self.conversations = ConversationStore(settings.conversation_dir)
        self.llm = LLMService(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            model_name=settings.model_name,
            api_keys=list(settings.openai_api_keys) if getattr(settings, "openai_api_keys", None) else None,
        )
        self.tts = TTSService(
            voice=settings.tts_voice,
            host=settings.tts_host,
            port=settings.tts_port,
            startup_timeout=settings.tts_startup_timeout,
            root_dir=ROOT_DIR,
        )
        self.tools = ToolService(
            groq_api_key=settings.groq_api_key,
            google_ai_api_key=settings.google_ai_api_key,
            tavily_api_key=settings.tavily_api_key,
            openai_api_key=settings.openai_api_key,
            openai_base_url=settings.openai_base_url,
            model_name=settings.model_name,
        )
        self.tts_available = False

    def startup(self) -> None:
        import threading

        def _bg_tts():
            try:
                if self.tts.start():
                    self.tts_available = True
                    logger.info("[AVA-SERVER] Local Piper TTS engine started.")
                else:
                    logger.warning("[AVA-SERVER] Unable to start Piper TTS engine. Running without local TTS.")
            except Exception as e:
                logger.warning(f"[AVA-SERVER] Note on TTS startup: {e}")

        threading.Thread(target=_bg_tts, daemon=True, name="TTSStartup").start()

        # Start background memory dreamer agent
        try:
            from memorySystem import memoryDreamer
            threading.Thread(
                target=memoryDreamer.DreamerStart,
                args=(self.conversations,),
                daemon=True,
                name="MemoryDreamer",
            ).start()
            logger.info("[AVA-SERVER] Background Memory Dreamer agent started with conversation store.")
        except Exception as e:
            logger.warning(f"[AVA-SERVER] Note on Memory Dreamer startup: {e}")

        # Initialize server plugins, skills, and MCP tools
        try:
            server_tool_registry.init()
        except Exception as e:
            logger.warning(f"[AVA-SERVER] Note on Server Tool Registry startup: {e}")

    def shutdown(self) -> None:
        self.tts.stop()
        try:
            server_tool_registry.stop_all()
        except Exception as e:
            pass


# Instantiate controller singleton
server_core = AvaServer()

# Create FastAPI application
app = FastAPI(title="AVA Central Server", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Web Client Directory (lives in Clients/web)
WEB_DIR = ROOT_DIR / "Clients" / "web"
WEB_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="web_static")

# Uploads Storage Directory
UPLOADS_DIR = SERVER_DIR / "data" / "uploads"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(UPLOADS_DIR)), name="uploads_static")


@app.on_event("startup")
async def on_startup():
    loop = asyncio.get_running_loop()
    server_core.loop = loop
    device_registry.set_event_loop(loop)
    server_core.startup()

    # Hook into TaskManager callbacks for live task panel updates & agent notification
    try:
        from App.core import TaskManager

        def _schedule_status_broadcast():
            asyncio.create_task(_broadcast_tasks_update())

        def _schedule_task_completion(t):
            print(f"[AVA-SERVER] Scheduling task completion callback for {getattr(t, 'id', '')}", flush=True)
            asyncio.create_task(_handle_local_task_completed(t))

        def _on_task_status_changed(task):
            if loop and loop.is_running():
                loop.call_soon_threadsafe(_schedule_status_broadcast)

        def _on_task_completed(task):
            if loop and loop.is_running():
                loop.call_soon_threadsafe(_schedule_task_completion, task)

        TaskManager.add_task_status_callback(_on_task_status_changed)
        TaskManager.add_task_completion_callback(_on_task_completed)
        logger.info("[AVA-SERVER] Hooked into TaskManager live status and completion callbacks.")
    except Exception as e:
        logger.warning(f"[AVA-SERVER] Note on TaskManager hook: {e}")

    # Hook Blackboard thoughts and steps to live WebSocket broadcast
    try:
        async def _broadcast_bb_msg(ev_type: str, payload: dict):
            msg = {
                "type": "thinking" if ev_type == "thinking" else "blackboard_event",
                "event": ev_type,
                "data": payload,
            }
            if ev_type == "thinking":
                msg["label"] = payload.get("text", "")
                msg["status"] = "completed" if payload.get("stage") == "completed" else "start"
                msg["ticket_id"] = payload.get("ticket_id")

            for dev in device_registry.list_devices(online_only=True):
                d_id = dev.get("device_id")
                if d_id:
                    await device_registry.send_to_device(d_id, msg)

        def _on_bb_event(ev_type: str, payload: dict):
            if loop and loop.is_running():
                asyncio.run_coroutine_threadsafe(_broadcast_bb_msg(ev_type, payload), loop)

        blackboard.add_listener(_on_bb_event)
        logger.info("[AVA-SERVER] Hooked Blackboard event stream to device WebSocket broadcast.")
    except Exception as e:
        logger.warning(f"[AVA-SERVER] Note on Blackboard broadcast hook: {e}")

    # Start System Sentinel if enabled
    if get_settings().sentinels_enabled:
        try:
            sys_sentinel = SystemSentinel(interval_sec=15.0)
            sys_sentinel.start()
            logger.info("[AVA-SERVER] Continuous SystemSentinel flight recorder started.")
        except Exception as e:
            logger.warning(f"[AVA-SERVER] SystemSentinel start error: {e}")



@app.on_event("shutdown")
def on_shutdown():
    server_core.shutdown()


# Web Client UI Route
@app.get("/")
@app.get("/web")
@app.get("/web/")
async def serve_web_client():
    index_path = WEB_DIR / "index.html"
    if index_path.exists():
        return FileResponse(
            str(index_path),
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )
    return JSONResponse({"status": "Web client files not yet built in Clients/web/"})


@app.get("/devices")
@app.get("/devices/")
@app.get("/devices.html")
async def serve_devices_page():
    devices_path = WEB_DIR / "devices.html"
    if devices_path.exists():
        return FileResponse(
            str(devices_path),
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )
    return JSONResponse({"status": "Devices management page not found in Clients/web/"}, status_code=404)


# ── REST API Endpoints ────────────────────────────────────────────────────────

@app.get("/health")
@app.post("/health")
async def health():
    return {"status": "ok"}


def _mask_key(key: Optional[str]) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "..." + key[-3:]
    return f"...{key[-4:]}"


@app.get("/api/settings")
async def api_get_settings():
    """Returns current server configuration with masked sensitive secrets."""
    s = get_settings()
    return {
        "ok": True,
        "settings": {
            "host": s.host,
            "port": s.port,
            "model_name": s.model_name,
            "openai_base_url": s.openai_base_url,
            "has_openai_key": bool(s.openai_api_key),
            "masked_openai_key": _mask_key(s.openai_api_key),
            "openai_key_count": len(s.openai_api_keys),
            "tts_voice": s.tts_voice,
            "tts_host": s.tts_host,
            "tts_port": s.tts_port,
            "groq_api_key": _mask_key(s.groq_api_key),
            "tavily_api_key": _mask_key(s.tavily_api_key),
            "google_ai_api_key": _mask_key(s.google_ai_api_key),
            "dual_layer_enabled": s.dual_layer_enabled,
            "layer1_model": s.layer1_model,
            "layer2_model": s.layer2_model,
            "sentinels_enabled": s.sentinels_enabled,
        },
    }


@app.post("/api/settings")
async def api_save_settings(request: Request):
    """Update Server/settings.json, reload environment, and return updated settings."""
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid JSON body"}, status_code=400)

    if not isinstance(data, dict):
        return JSONResponse({"ok": False, "error": "Settings must be a JSON object"}, status_code=400)

    allowed_keys = {
        "DUAL_LAYER_ENABLED", "dual_layer_enabled",
        "LAYER1_MODEL", "layer1_model",
        "LAYER2_MODEL", "layer2_model",
        "SENTINELS_ENABLED", "sentinels_enabled",
        "MODEL_NAME", "model_name",
        "OPENAI_BASE_URL", "openai_base_url",
        "OPENAI_API_KEY", "openai_api_key",
        "GROQ_API_KEY", "groq_api_key",
        "TAVILY_API_KEY", "tavily_api_key",
        "GOOGLE_AI_API_KEY", "google_ai_api_key",
        "tts_voice",
    }

    updates = {}
    for k, v in data.items():
        if k in allowed_keys:
            norm_k = k.upper() if k.lower() in (
                "dual_layer_enabled", "layer1_model", "layer2_model",
                "sentinels_enabled", "model_name", "openai_base_url",
                "openai_api_key", "groq_api_key", "tavily_api_key", "google_ai_api_key"
            ) else k
            updates[norm_k] = v

    updated_settings = save_settings(updates)
    logger.info(f"[ServerSettings] Updated settings: {list(updates.keys())}")
    return {
        "ok": True,
        "message": "Settings updated successfully.",
        "dual_layer_enabled": updated_settings.dual_layer_enabled,
        "sentinels_enabled": updated_settings.sentinels_enabled,
    }


@app.get("/api/blackboard/jobs")
async def api_blackboard_jobs(limit: int = 50):
    """List recent and active background jobs from Blackboard."""
    jobs = blackboard.list_tickets(limit=limit)
    return {"ok": True, "count": len(jobs), "jobs": jobs}


@app.get("/api/blackboard/jobs/{ticket_id}")
async def api_blackboard_job_detail(ticket_id: str):
    """Retrieve full details, steps, and live thought logs for a job ticket."""
    ticket = blackboard.get_ticket(ticket_id)
    if not ticket:
        return JSONResponse({"ok": False, "error": f"Ticket {ticket_id} not found"}, status_code=404)
    thoughts = blackboard.get_thoughts(ticket_id)
    return {
        "ok": True,
        "ticket": ticket.to_dict(),
        "thoughts": thoughts,
    }


@app.post("/api/blackboard/jobs/{ticket_id}/cancel")
async def api_blackboard_job_cancel(ticket_id: str):
    """Cancel an in-progress Layer 2 job ticket."""
    success = blackboard.cancel_ticket(ticket_id)
    return {"ok": success, "ticket_id": ticket_id}


@app.get("/api/blackboard/telemetry")
async def api_blackboard_telemetry(sentinel: Optional[str] = None, limit: int = 50):
    """Query recent Sentinel flight-recorder telemetry entries."""
    history = blackboard.get_telemetry_history(sentinel=sentinel, limit=limit)
    return {"ok": True, "count": len(history), "telemetry": history}


# User Presence State ('present' | 'away')
CURRENT_USER_PRESENCE: str = "present"
PENDING_AWAY_ALERTS: List[Dict[str, Any]] = []


def get_current_user_presence() -> str:
    global CURRENT_USER_PRESENCE
    return CURRENT_USER_PRESENCE


def set_current_user_presence(presence: str) -> str:
    global CURRENT_USER_PRESENCE
    if presence in ("present", "away"):
        CURRENT_USER_PRESENCE = presence
    return CURRENT_USER_PRESENCE


async def dispatch_sentinel_alert(
    sentinel: str,
    title: str,
    message: str,
    severity: str = "WARN",
    raw_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Closed-Loop Sentinel Pipeline:
    1. Sentinel telemetry is continuously logged to Blackboard Flight Recorder.
    2. Layer 2 Chief of Staff diagnoses the issue using flight recorder snapshots.
    3. Layer 1 Executive Avatar checks user presence.
    4. If present: Formats a natural assistant briefing and commits it into
       server_core.conversations so all follow-ups ("what are the steps to fix this?")
       have complete conversation memory and context!
    5. If away: Queues it quietly without interrupting.
    """
    # 1. Flight recorder ledger
    entry = blackboard.record_telemetry(
        sentinel=sentinel,
        data=raw_data or {"message": message, "title": title},
        level=severity,
    )

    # 2. Layer 2 diagnosis
    diagnosis = layer2_orchestrator.diagnose_sentinel_alert({
        "sentinel": sentinel,
        "alert_title": title,
        "message": message,
        "flight_recorder_snapshot": blackboard.get_telemetry_history(sentinel=sentinel, limit=10),
    })

    # 3. Layer 1 human assistant briefing format
    report_text = layer1_avatar.format_sentinel_report(
        alert={"title": title, "message": message, "severity": severity, "sentinel": sentinel},
        diagnosis=diagnosis,
    )

    is_present = (get_current_user_presence() == "present")

    # 4. If user is present, commit to conversation store and broadcast as assistant dialogue
    active_conv_id = None
    if is_present:
        try:
            recent_convs = server_core.conversations.list_recent(limit=1)
            active_conv_id = recent_convs[0]["id"] if recent_convs else "default_session"
            server_core.conversations.add_message(
                role="assistant",
                content=report_text,
                conv_id=active_conv_id,
                client_id="ava_layer1",
            )
        except Exception as ex:
            logger.warning(f"Could not persist sentinel alert to conversation: {ex}")

        # Broadcast assistant message to all connected clients
        out_msg = {
            "type": "assistant_response",
            "conversation_id": active_conv_id,
            "content": report_text,
            "metadata": {
                "cognitive_mode": "sentinel_briefing",
                "is_sentinel_alert": True,
                "severity": severity,
                "sentinel": sentinel,
                "entry_id": entry.entry_id,
                "diagnosis": diagnosis,
            },
        }
        for dev in device_registry.list_devices(online_only=True):
            d_id = dev.get("device_id")
            if d_id:
                asyncio.create_task(device_registry.send_to_device(d_id, out_msg))
    else:
        PENDING_AWAY_ALERTS.append({
            "sentinel": sentinel,
            "title": title,
            "message": message,
            "severity": severity,
            "diagnosis": diagnosis,
            "report_text": report_text,
            "timestamp": time.time(),
        })
        logger.info(f"[Sentinel:{sentinel}] User away. Alert queued for return briefing: {title}")

    return {
        "ok": True,
        "entry_id": entry.entry_id,
        "diagnosis": diagnosis,
        "delivered_live": is_present,
        "presence": get_current_user_presence(),
        "report_text": report_text,
    }


@app.get("/api/presence")
async def api_get_presence():
    """Get current user presence state."""
    return {"ok": True, "presence": get_current_user_presence()}


@app.post("/api/presence")
async def api_set_presence(request: Request):
    """Set user presence ('present' | 'away') and broadcast to clients."""
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid JSON"}, status_code=400)

    new_pres = str(data.get("presence", "present")).lower()
    set_current_user_presence(new_pres)

    # Broadcast to all connected devices
    up_msg = {"type": "user_presence_updated", "presence": new_pres}
    for dev in device_registry.list_devices(online_only=True):
        d_id = dev.get("device_id")
        if d_id:
            asyncio.create_task(device_registry.send_to_device(d_id, up_msg))

    logger.info(f"[Presence] User presence updated to: {new_pres}")
    return {"ok": True, "presence": new_pres}


@app.post("/api/events/trigger")
async def api_events_trigger(request: Request):
    """
    Mock/external event injector to test how AVA reacts to sentinel alerts.
    """
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid JSON"}, status_code=400)

    sentinel_name = str(payload.get("sentinel", "system_sentinel"))
    severity = str(payload.get("severity", "WARN")).upper()
    title = str(payload.get("title", f"Event from {sentinel_name}"))
    message = str(payload.get("message", "Triggered test alert."))

    res = await dispatch_sentinel_alert(
        sentinel=sentinel_name,
        title=title,
        message=message,
        severity=severity,
        raw_data=payload,
    )

    logger.info(f"[EventTrigger] Injected {severity} event from '{sentinel_name}': {title} (delivered_live={res['delivered_live']})")
    return res


@app.post("/api/chat/dual_layer")
async def api_chat_dual_layer(request: Request):
    """Direct endpoint to route a query through Layer 1 Avatar."""
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid JSON"}, status_code=400)

    message = str(data.get("message", "")).strip()
    client_id = str(data.get("client_id", "web_client")).strip()
    conv_id = str(data.get("conversation_id", "")).strip()

    if not message:
        return JSONResponse({"ok": False, "error": "Message is required"}, status_code=400)

    res = await layer1_avatar.route_turn(
        user_text=message,
        client_id=client_id,
        conversation_id=conv_id,
    )
    return {"ok": True, **res}



def _guess_mime(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    mapping = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".bmp": "image/bmp",
        ".svg": "image/svg+xml",
        ".pdf": "application/pdf",
        ".txt": "text/plain",
        ".md": "text/markdown",
        ".json": "application/json",
        ".py": "text/x-python",
        ".js": "text/javascript",
        ".html": "text/html",
        ".csv": "text/csv",
    }
    return mapping.get(ext, "application/octet-stream")


async def _execute_view_image_tool(img_path: str, query: str = "") -> Any:
    raw_path = str(img_path or "").strip().strip('"').strip("'")
    if not raw_path:
        return {"status": "error", "content": "Missing image_path"}

    candidates = [
        Path(raw_path),
        UPLOADS_DIR / Path(raw_path).name,
        UPLOADS_DIR / raw_path,
        SERVER_DIR / raw_path,
        ROOT_DIR / raw_path,
    ]

    target: Optional[Path] = None
    for cand in candidates:
        try:
            if cand.is_file():
                target = cand
                break
        except Exception:
            continue

    if not target or not target.is_file():
        return {"status": "error", "content": f"Image file not found at '{raw_path}'. Checked server uploads and local storage."}

    try:
        data = target.read_bytes()
        mime = _guess_mime(target.name)
        if not mime.startswith("image/"):
            mime = "image/png"

        b64_str = base64.b64encode(data).decode("ascii")
        data_url = f"data:{mime};base64,{b64_str}"

        analysis_content = ""
        try:
            res = await asyncio.to_thread(
                server_core.tools.analyze_image,
                image_base64=b64_str,
                query=query or "Describe what is shown in this image in detail and transcribe all visible text accurately.",
                image_path=str(target),
            )
            if isinstance(res, dict) and res.get("status") == "success":
                analysis_content = res.get("content", "")
        except Exception:
            pass

        summary_text = f"Loaded image '{target.name}' from {target.resolve()} ({len(data)} bytes)."
        if analysis_content:
            summary_text = f"{summary_text}\n\nVision Analysis:\n{analysis_content}"

        return [
            {
                "type": "text",
                "text": summary_text,
            },
            {
                "type": "image_url",
                "image_url": {
                    "url": data_url,
                    "detail": "high",
                },
            },
        ]
    except Exception as exc:
        return {"status": "error", "content": f"Failed to load and process image '{raw_path}': {exc}"}


@app.post("/api/upload")
async def api_upload_file(request: Request):
    """
    Upload file(s) to Server/data/uploads/.
    Returns file metadata including server_path for agent consumption.
    """
    try:
        form = await request.form()
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"Invalid form data: {e}"}, status_code=400)

    uploaded_files = []
    for key, value in form.items():
        if hasattr(value, "filename") and hasattr(value, "read"):
            orig_name = Path(value.filename).name if value.filename else "upload.bin"
            clean_name = re.sub(r'[^\w\.\-_]', '_', orig_name)
            if not clean_name:
                clean_name = f"file_{int(time.time())}.bin"

            target_path = UPLOADS_DIR / clean_name
            if target_path.exists():
                stem = target_path.stem
                suffix = target_path.suffix
                clean_name = f"{stem}_{int(time.time()*1000) % 100000}{suffix}"
                target_path = UPLOADS_DIR / clean_name

            content = await value.read()
            with open(target_path, "wb") as f:
                f.write(content)

            ext = target_path.suffix.lower()
            mime = getattr(value, "content_type", None) or _guess_mime(clean_name)
            is_image = ext in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".svg") or str(mime).startswith("image/")

            abs_path = str(target_path.resolve())
            uploaded_files.append({
                "filename": clean_name,
                "server_path": abs_path,
                "relative_path": f"Server/data/uploads/{clean_name}",
                "url": f"/uploads/{clean_name}",
                "size": len(content),
                "mime": mime,
                "is_image": is_image,
            })

    if not uploaded_files:
        return JSONResponse({"ok": False, "error": "No file uploaded in request"}, status_code=400)

    return {"ok": True, "files": uploaded_files}


@app.get("/memories")
async def memories():
    mem_dir = ROOT_DIR / "Server" / "data" / "memories"
    mem_path = mem_dir / "memories.txt"
    beh_path = mem_dir / "behaviours.txt"
    memories = mem_path.read_text(encoding="utf-8") if mem_path.exists() else ""
    behaviours = beh_path.read_text(encoding="utf-8") if beh_path.exists() else ""
    return {"memories": memories, "behaviours": behaviours}


@app.get("/usage")
@app.post("/usage")
@app.get("/token_usage")
@app.post("/token_usage")
async def usage(request: Request):
    payload = {}
    if request.method == "POST":
        try:
            payload = await request.json()
        except Exception:
            pass
    day = payload.get("date") or payload.get("day") or request.query_params.get("date")
    days_limit = int(payload.get("days_limit") or request.query_params.get("days_limit") or 14)
    summary = get_usage_summary(day=day, include_days=True, days_limit=days_limit)
    return summary


@app.post("/conversation/start")
async def conversation_start(request: Request):
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    client_id = payload.get("client_id", "default")
    conv_id = server_core.conversations.start(client_id=client_id)
    return {"ok": True, "conversation_id": conv_id}


@app.post("/conversation/message")
async def conversation_message(request: Request):
    payload = await request.json()
    role = str(payload.get("role", "user"))
    content = str(payload.get("content", ""))
    tool_call_id = payload.get("tool_call_id")
    conv_id = payload.get("conversation_id")
    client_id = payload.get("client_id", "default")

    c_id = server_core.conversations.add_message(
        role=role,
        content=content,
        tool_call_id=str(tool_call_id) if tool_call_id else None,
        conv_id=conv_id,
        client_id=client_id,
    )
    return {"ok": True, "conversation_id": c_id}


@app.post("/conversation/save")
async def conversation_save():
    server_core.conversations.save_current()
    return {"ok": True}


@app.post("/conversation/sync")
async def conversation_sync(request: Request):
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if "conversations" in payload and isinstance(payload["conversations"], list):
        synced = []
        for c in payload["conversations"]:
            if isinstance(c, dict) and c.get("id"):
                cid = server_core.conversations.import_thread(c)
                synced.append(cid)
        return {"ok": True, "synced_count": len(synced), "conversation_ids": synced}
    conv_data = payload.get("conversation") or payload
    if not isinstance(conv_data, dict) or not conv_data.get("id"):
        return JSONResponse({"error": "Missing conversation payload or id"}, status_code=400)
    conv_id = server_core.conversations.import_thread(conv_data)
    return {"ok": True, "conversation_id": conv_id}


@app.post("/conversation/consolidate")
async def conversation_consolidate(request: Request):
    """Trigger Dreamer chat compression & combination on-demand."""
    from memorySystem import memoryDreamer
    payload = {}
    try:
        payload = await request.json()
    except Exception:
        pass
    all_chats = bool(payload.get("all_chats") or payload.get("all") or payload.get("force_all"))
    min_age_val = payload.get("min_inactive_seconds")
    min_age = int(min_age_val) if min_age_val is not None else (0 if all_chats else 900)
    dry_run = bool(payload.get("dry_run", False))

    res = await asyncio.to_thread(
        memoryDreamer.compress_and_combine_small_chats,
        conv_store=server_core.conversations,
        min_inactive_seconds=min_age,
        all_chats=all_chats,
        dry_run=dry_run,
    )
    return {"ok": True, "result": res}


@app.get("/conversation/list")
@app.post("/conversation/list")
async def conversation_list(request: Request):
    limit = 50
    if request.method == "POST":
        try:
            payload = await request.json()
            limit = int(payload.get("limit", 50))
        except Exception:
            pass
    elif request.method == "GET":
        limit = int(request.query_params.get("limit", 50))
    return {"conversations": server_core.conversations.list_recent(limit=limit)}


@app.post("/conversation/delete")
@app.delete("/conversation/delete")
async def conversation_delete(request: Request):
    conv_id = request.query_params.get("id") or request.query_params.get("conversation_id")
    if request.method == "POST":
        try:
            payload = await request.json()
            conv_id = conv_id or payload.get("id") or payload.get("conversation_id")
        except Exception:
            pass
    if not conv_id:
        return JSONResponse({"error": "Missing conversation_id"}, status_code=400)
    success = server_core.conversations.delete_thread(str(conv_id).strip())
    return {"ok": success, "conversation_id": conv_id}


def _format_task_payload(t) -> dict:
    meta = getattr(t, "metadata", {}) or {}
    cmd = (
        getattr(t, "command", None)
        or meta.get("command")
        or meta.get("objective")
        or getattr(t, "original_query", None)
        or getattr(t, "description", None)
        or getattr(t, "name", None)
        or f"{getattr(t, 'task_type', 'Task').capitalize()} Task"
    )
    title = getattr(t, "name", None) or getattr(t, "original_query", None) or getattr(t, "description", None) or cmd
    raw_status = getattr(t, "status", "running")
    stat = raw_status.value if hasattr(raw_status, "value") else str(raw_status)
    prog = 0.65 if stat == "running" else (1.0 if stat == "completed" else 0.0)
    created_val = getattr(t, "created_at", None)
    created_str = created_val.isoformat() if hasattr(created_val, "isoformat") else str(created_val or "")
    return {
        "id": getattr(t, "id", ""),
        "name": title,
        "title": title,
        "command": cmd,
        "tool": getattr(t, "task_type", "task"),
        "task_type": getattr(t, "task_type", "general"),
        "status": stat,
        "statusText": stat,
        "progress": prog,
        "created_at": created_str,
        "conversation_id": getattr(t, "conversation_id", "") or "",
    }


@app.get("/tasks/active")
async def tasks_active():
    try:
        from App.core.TaskManager import get_registry
        registry = get_registry()
        running = registry.get_running_tasks()
        return {"ok": True, "tasks": [_format_task_payload(t) for t in running]}
    except Exception as e:
        return {"ok": False, "error": str(e), "tasks": []}


@app.get("/tasks/list")
async def tasks_list():
    try:
        from App.core.TaskManager import get_registry
        registry = get_registry()
        all_tasks = registry.get_all_tasks()
        return {"ok": True, "tasks": [_format_task_payload(t) for t in all_tasks[:50]]}
    except Exception as e:
        return {"ok": False, "error": str(e), "tasks": []}


@app.post("/tasks/cancel")
async def tasks_cancel(request: Request):
    try:
        data = await request.json()
        task_id = str(data.get("task_id") or data.get("id") or "").strip()
        if not task_id:
            return JSONResponse({"ok": False, "error": "Missing task_id"}, status_code=400)
        from App.core.TaskManager import get_registry
        registry = get_registry()
        success = registry.cancel_task(task_id)
        return {"ok": success, "task_id": task_id}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)



@app.get("/api/history")
@app.get("/conversation/history")
@app.post("/conversation/history")
async def conversation_history(request: Request):
    conv_id = request.query_params.get("id") or request.query_params.get("conversation_id")
    client_id = request.query_params.get("client_id", "default")
    if request.method == "POST":
        try:
            payload = await request.json()
            conv_id = conv_id or payload.get("id") or payload.get("conversation_id")
            client_id = payload.get("client_id", client_id)
        except Exception:
            pass
    if not conv_id:
        conv_id = server_core.conversations.client_active_conv.get(client_id)
    if conv_id:
        thread = server_core.conversations.get_thread(conv_id)
        if thread:
            return {"conversation_id": conv_id, "messages": thread.get("messages", [])}
    return {"conversation_id": conv_id, "messages": []}


@app.post("/tts")
async def tts_endpoint(request: Request):
    if not server_core.tts_available:
        return JSONResponse({"error": "Piper TTS unavailable"}, status_code=503)
    payload = await request.json()
    text = str(payload.get("text", "")).strip()
    if not text:
        return JSONResponse({"error": "Missing 'text'"}, status_code=400)
    wav_bytes = server_core.tts.synthesize_bytes(text)
    if not wav_bytes:
        return JSONResponse({"error": "TTS synthesis failed"}, status_code=500)
    return Response(content=wav_bytes, media_type="audio/wav")


@app.post("/stt")
async def stt_endpoint(request: Request):
    return {"text": "STT transcription mock", "confidence": 0.9}


# ── Device Hub Endpoints ──────────────────────────────────────────────────────

@app.get("/devices/list")
@app.post("/devices/list")
async def devices_list(request: Request):
    online_only = request.query_params.get("online_only", "false").lower() == "true"
    devices = device_registry.list_devices(online_only=online_only)
    return {"devices": devices}


@app.post("/devices/execute")
@app.post("/devices/execute_tool")
async def devices_execute(request: Request):
    payload = await request.json()
    target_device = str(payload.get("target_device") or payload.get("device_id") or "caller").strip()
    tool_name = str(payload.get("tool_name") or payload.get("tool") or payload.get("action") or "").strip()
    arguments = payload.get("arguments") or payload.get("parameters") or {}
    caller_id = payload.get("caller_id") or payload.get("caller_device_id")
    timeout = float(payload.get("timeout", 15.0))

    if not tool_name:
        return JSONResponse({"error": "Missing 'tool_name'"}, status_code=400)

    res = await device_registry.execute_tool_async(
        target_device=target_device,
        tool_name=tool_name,
        arguments=arguments,
        caller_id=caller_id,
        timeout=timeout,
    )
    status_code = 200 if res.get("status") == "success" else 400
    return JSONResponse(res, status_code=status_code)


@app.get("/api/devices")
async def api_devices_list(request: Request):
    online_only = request.query_params.get("online_only", "false").lower() == "true"
    devices = device_registry.list_devices(online_only=online_only)
    return {"ok": True, "devices": devices}


@app.post("/api/devices/rename")
async def api_devices_rename(request: Request):
    payload = await request.json()
    device_id = str(payload.get("device_id") or "").strip()
    name = str(payload.get("name") or "").strip()
    if not device_id or not name:
        return JSONResponse({"ok": False, "error": "Missing device_id or name"}, status_code=400)
    ok = device_registry.rename_device(device_id, name)
    if not ok:
        return JSONResponse({"ok": False, "error": f"Device '{device_id}' not found"}, status_code=404)
    return {"ok": True, "devices": device_registry.list_devices()}


@app.post("/api/devices/remove")
async def api_devices_remove(request: Request):
    payload = await request.json()
    device_id = str(payload.get("device_id") or "").strip()
    if not device_id:
        return JSONResponse({"ok": False, "error": "Missing device_id"}, status_code=400)
    ok = device_registry.unregister_device(device_id)
    if not ok:
        return JSONResponse({"ok": False, "error": f"Device '{device_id}' not found"}, status_code=404)
    return {"ok": True, "devices": device_registry.list_devices()}


@app.post("/api/devices/ping")
async def api_devices_ping(request: Request):
    payload = await request.json()
    device_id = str(payload.get("device_id") or "").strip()
    if not device_id:
        return JSONResponse({"ok": False, "error": "Missing device_id"}, status_code=400)
    res = await device_registry.ping_device(device_id)
    return {"ok": res.get("status") == "online", "result": res}


# ── Tool Execution Endpoint ───────────────────────────────────────────────────

@app.get("/tools")
@app.post("/tools/schema")
async def tools_schema():
    return {"tools": get_all_server_tool_schemas()}


async def _execute_named_tool(name: str, arguments: dict[str, Any], caller_device_id: Optional[str] = None) -> Any:
    name = str(name).strip()
    # Normalize common tool name variations and aliases
    if name in ("code_executor", "sandbox", "execute_code", "python"):
        name = "code_execute"
    elif name in ("vision", "describe_image"):
        name = "image_analysis"
    elif name in ("view_image", "load_image", "inspect_image", "show_image"):
        name = "view_image"
    elif name in ("deep_research",):
        name = "research"
    elif name in ("search", "google_search"):
        name = "google_ai"

    if name == "google_ai":
        try:
            return await asyncio.to_thread(server_core.tools.google_ai_search, query=arguments.get("query", ""))
        except Exception as e:
            return {"status": "error", "content": f"Google AI error: {e}"}
    elif name == "view_image":
        img_p = arguments.get("image_path") or arguments.get("path") or arguments.get("file_path") or ""
        q = arguments.get("query") or "Describe this image in detail and transcribe all visible text accurately."
        return await _execute_view_image_tool(img_p, q)
    elif name == "image_analysis":
        img_p = arguments.get("image_path") or arguments.get("path")
        if img_p and not arguments.get("image_base64"):
            return await _execute_view_image_tool(img_p, arguments.get("query", "Describe this image"))
        try:
            return await asyncio.to_thread(
                server_core.tools.analyze_image,
                image_base64=arguments.get("image_base64", ""),
                query=arguments.get("query", "Describe this image"),
                image_path=img_p,
            )
        except Exception as e:
            return {"status": "error", "content": f"Image analysis error: {e}"}
    elif name == "code_execute":
        try:
            return await asyncio.to_thread(
                server_core.tools.execute_code,
                code=arguments.get("code", ""),
                timeout=int(arguments.get("timeout", 5)),
            )
        except Exception as e:
            return {"status": "error", "content": f"Code execution error: {e}"}
    elif name == "research":
        try:
            return await asyncio.to_thread(
                server_core.tools.research,
                question=arguments.get("question") or arguments.get("query", ""),
                level=int(arguments.get("level", 3)),
            )
        except Exception as e:
            return {"status": "error", "content": f"Research error: {e}"}
    elif name in ("store_screenshot", "store_image"):
        return server_core.tools.store_screenshot(
            image_base64=arguments.get("image_base64", ""),
            filename=arguments.get("filename"),
            mime=arguments.get("mime"),
        )
    elif name in ("device_location", "device_battery", "device_ring", "device_music", "device_time", "device_tool"):
        target = arguments.get("target_device", "caller")
        tool_name = arguments.get("tool_name")
        if name == "device_location":
            tool_name = "get_location"
        elif name == "device_battery":
            tool_name = "get_battery_status"
        elif name == "device_ring":
            tool_name = "ring_device"
        elif name == "device_music":
            tool_name = "music"
        elif name in ("device_time", "timedate"):
            tool_name = "timedate"
        sub_args = arguments.get("arguments")
        if not isinstance(sub_args, dict):
            sub_args = {k: v for k, v in arguments.items() if k not in ("target_device", "tool_name")}
        res = await device_registry.execute_tool_async(
            target_device=target,
            tool_name=tool_name,
            arguments=sub_args,
            caller_id=caller_device_id,
        )
        if res.get("status") == "error" and tool_name in ("timedate", "time"):
            now = datetime.datetime.now()
            res = {
                "status": "success",
                "device_id": "system_clock",
                "tool": "timedate",
                "result": {
                    "local_time": now.strftime("%I:%M:%S %p"),
                    "local_date": now.strftime("%Y-%m-%d"),
                    "timezone": "System Local Time",
                },
            }
        return res
    elif name == "tool_search":
        q = arguments.get("query", "")
        target = arguments.get("target_device")
        results = device_registry.search_tools(query=q, target_device=target)
        if not target or target in ("server", "local", "all"):
            for tname, tinfo in server_tool_registry.get_tools().items():
                tdesc = tinfo.get("description", "")
                if not q or q.lower() in tname.lower() or q.lower() in tdesc.lower():
                    results.append({
                        "device_id": "server",
                        "device_name": "AVA Server",
                        "online": True,
                        "tool": tname,
                        "description": tdesc,
                        "parameters": tinfo.get("params", {}),
                    })
        return {"query": q, "tools": results}
    elif name == "background_task":
        try:
            from App.core.TaskManager import dispatch_background_task
            task_type = arguments.get("task_type", "research")
            task_id, msg = dispatch_background_task(task_type=task_type, **arguments)
            return {"status": "started" if task_id else "error", "task_id": task_id, "message": msg}
        except Exception as e:
            return JSONResponse({"status": "error", "error": f"Failed to start background task: {e}"}, status_code=500)
    elif name == "get_background_tasks_status":
        try:
            from App.core.TaskManager import get_running_tasks_summary
            return {"status": "success", "summary": get_running_tasks_summary()}
        except Exception as e:
            return JSONResponse({"status": "error", "error": str(e)}, status_code=500)
    elif name == "manage_background_tasks":
        try:
            from App.core.TaskManager import manage_background_tasks
            act = arguments.get("action", "list")
            res = manage_background_tasks(action=act, **arguments)
            return {"status": "success", "result": res}
        except Exception as e:
            return JSONResponse({"status": "error", "error": str(e)}, status_code=500)
    elif server_tool_registry.has_tool(name):
        res = await server_tool_registry.execute_async(name, arguments)
        return JSONResponse(res if isinstance(res, (dict, list)) else {"result": res})
    else:
        return JSONResponse({"error": f"Unknown tool: {name}"}, status_code=400)


@app.post("/tools/execute")
async def tools_execute(request: Request):
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    name = str(payload.get("name", "")).strip()
    arguments = payload.get("arguments", {})
    if not isinstance(arguments, dict):
        arguments = {}
    if not name:
        return JSONResponse({"error": "Missing 'name'"}, status_code=400)
    return await _execute_named_tool(name, arguments, caller_device_id=payload.get("caller_device_id"))


@app.post("/tools/code_execute")
@app.post("/tools/image_analysis")
@app.post("/tools/research")
@app.post("/tools/google_ai")
@app.post("/tools/store_screenshot")
@app.post("/tools/{tool_name}")
async def tools_execute_direct(request: Request, tool_name: Optional[str] = None):
    """Direct route for tool calls by path (e.g. POST /tools/code_execute)."""
    if not tool_name:
        tool_name = request.url.path.rstrip("/").split("/")[-1]
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    # If payload is wrapped in {"arguments": ...}, unwrap it; otherwise payload is the arguments dict
    if isinstance(payload, dict) and "arguments" in payload and isinstance(payload.get("arguments"), dict):
        arguments = payload["arguments"]
    elif isinstance(payload, dict):
        arguments = payload
    else:
        arguments = {}

    caller_id = payload.get("caller_device_id") if isinstance(payload, dict) else None
    return await _execute_named_tool(tool_name, arguments, caller_device_id=caller_id)


# ── LLM Generation with Hot Device Tool Resolution ────────────────────────────

@app.post("/generate")
async def generate(request: Request):
    payload = await request.json()
    messages = payload.get("messages", [])
    tools = payload.get("tools") or get_all_server_tool_schemas()
    caller_device_id = payload.get("caller_device_id") or payload.get("caller_id")
    auto_resolve = bool(payload.get("auto_resolve_tools", False))

    include_ctx = payload.get("include_server_context", True)
    if isinstance(include_ctx, str):
        include_ctx = include_ctx.strip().lower() not in ("0", "false", "no", "off")

    # Build system prompt with identity, tool routing, and behavior rules
    system_prompt = get_server_system_prompt()

    now_dt = datetime.datetime.now()
    clock_env = (
        f"[SYSTEM ENVIRONMENT & REAL-TIME CLOCK]\n"
        f"Current Time: {now_dt.strftime('%A, %B %d, %Y, %I:%M:%S %p')} ({time.tzname[0] if time.tzname else 'Local'})\n"
        f"Caller Device: {caller_device_id or 'web_client'}\n"
        f"Instruction: You already know the current real-world time, date, and day of the week from this clock. Answer time and date queries directly and immediately without calling device_time, unless the user specifically asks to check an external remote device."
    )
    context = f"{system_prompt}\n\n{clock_env}"

    c_id = payload.get("conversation_id") or ""
    if include_ctx:
        recent_ctx = server_core.conversations.get_recent_chats_context(
            current_conv_id=c_id,
            min_chats=5,
            updated_within_minutes=15,
        )
        if recent_ctx:
            context = f"{context}\n\n{recent_ctx}"

    # Inject connected and offline devices presence
    presence_summary = device_registry.get_presence_summary(caller_id=caller_device_id)
    if presence_summary:
        context = f"{context}\n\n{presence_summary}".strip()

    # Inject long-term + agent-saved memories (dreamer store + agent_memories.json)
    if include_ctx:
        try:
            from services.memory import get_memory_context
            mem_ctx = get_memory_context()
            if mem_ctx:
                context = f"{context}\n\n{mem_ctx}"
        except Exception as e:
            logger.warning(f"[Memory] Context injection failed: {e}")

    model = payload.get("model")
    model_tier = payload.get("model_tier")

    try:
        result = await asyncio.to_thread(
            server_core.llm.generate,
            messages=messages,
            tools=tools,
            context_message=context,
            model=model,
            model_tier=model_tier,
        )
    except Exception as e:
        logger.error(f"[LLM] Error generating response: {e}")
        return {
            "role": "assistant",
            "content": f"[AVA System Note] Could not connect to LLM backend: {e}. Check if local model server or API key is active.",
            "tool_calls": [],
        }

    # Server tool execution loop if auto_resolve is enabled
    all_tool_calls = []
    if auto_resolve and result.get("tool_calls"):
        cur_messages = list(messages)
        rounds = 0
        while result.get("tool_calls") and rounds < 5:
            rounds += 1
            all_tool_calls.extend(result["tool_calls"])
            cur_messages.append({
                "role": "assistant",
                "content": result.get("content"),
                "tool_calls": result.get("tool_calls"),
            })
            for tc in result["tool_calls"]:
                fn = tc.get("function", {})
                fn_name = fn.get("name")
                fn_args = (
                    json.loads(fn.get("arguments", "{}"))
                    if isinstance(fn.get("arguments"), str)
                    else (fn.get("arguments") or {})
                )
                tc_id = tc.get("id", f"call_{rounds}")

                # Dispatch tool
                if fn_name == "device_location":
                    target = fn_args.get("target_device", "caller")
                    t_res = await device_registry.execute_tool_async(
                        target, "get_location", fn_args, caller_id=caller_device_id
                    )
                elif fn_name == "device_battery":
                    target = fn_args.get("target_device", "caller")
                    t_res = await device_registry.execute_tool_async(
                        target, "get_battery_status", fn_args, caller_id=caller_device_id
                    )
                elif fn_name == "device_ring":
                    target = fn_args.get("target_device", "caller")
                    t_res = await device_registry.execute_tool_async(
                        target, "ring_device", fn_args, caller_id=caller_device_id
                    )
                elif fn_name == "device_music":
                    target = fn_args.get("target_device", "caller")
                    t_res = await device_registry.execute_tool_async(
                        target, "music", fn_args, caller_id=caller_device_id
                    )
                elif fn_name in ("device_time", "device_timedate", "timedate"):
                    target = fn_args.get("target_device", "caller")
                    if target in ("caller", "current", "me", "this", "here", "local", "") or (caller_device_id and str(target).strip().lower() == str(caller_device_id).strip().lower()):
                        now = datetime.datetime.now()
                        t_res = {
                            "status": "success",
                            "device_id": caller_device_id or "local",
                            "tool": "timedate",
                            "result": {
                                "local_time": now.strftime("%I:%M:%S %p"),
                                "local_date": now.strftime("%Y-%m-%d"),
                                "timezone": time.tzname[0] if time.tzname else "Local",
                            },
                        }
                    else:
                        t_res = await device_registry.execute_tool_async(
                            target, "timedate", fn_args, caller_id=caller_device_id, timeout=1.8
                        )
                        if t_res.get("status") == "error":
                            now = datetime.datetime.now()
                            t_res = {
                                "status": "success",
                                "device_id": "system_clock",
                                "tool": "timedate",
                                "result": {
                                    "local_time": now.strftime("%I:%M:%S %p"),
                                    "local_date": now.strftime("%Y-%m-%d"),
                                    "timezone": "System Local Time",
                                },
                            }
                elif fn_name == "device_tool":
                    target = fn_args.get("target_device", "caller")
                    t_name = fn_args.get("tool_name", "")
                    sub_args = fn_args.get("arguments")
                    if not isinstance(sub_args, dict):
                        sub_args = {k: v for k, v in fn_args.items() if k not in ("target_device", "tool_name")}
                    if t_name in ("timedate", "time", "get_time") and (target in ("caller", "current", "me", "this", "here", "local", "") or (caller_device_id and str(target).strip().lower() == str(caller_device_id).strip().lower())):
                        now = datetime.datetime.now()
                        t_res = {
                            "status": "success",
                            "device_id": caller_device_id or "local",
                            "tool": "timedate",
                            "result": {
                                "local_time": now.strftime("%I:%M:%S %p"),
                                "local_date": now.strftime("%Y-%m-%d"),
                                "timezone": time.tzname[0] if time.tzname else "Local",
                            },
                        }
                    else:
                        t_res = await device_registry.execute_tool_async(
                            target, t_name, sub_args, caller_id=caller_device_id
                        )
                        if t_res.get("status") == "error" and t_name in ("timedate", "time", "get_time"):
                            now = datetime.datetime.now()
                            t_res = {
                                "status": "success",
                                "device_id": "system_clock",
                                "tool": "timedate",
                                "result": {
                                    "local_time": now.strftime("%I:%M:%S %p"),
                                    "local_date": now.strftime("%Y-%m-%d"),
                                    "timezone": "System Local Time",
                                },
                            }
                elif fn_name == "tool_search":
                    q = fn_args.get("query", "")
                    t_dev = fn_args.get("target_device")
                    results = device_registry.search_tools(q, t_dev)
                    if not t_dev or t_dev in ("server", "local", "all"):
                        for tname, tinfo in server_tool_registry.get_tools().items():
                            tdesc = tinfo.get("description", "")
                            if not q or q.lower() in tname.lower() or q.lower() in tdesc.lower():
                                results.append({
                                    "device_id": "server",
                                    "device_name": "AVA Server",
                                    "online": True,
                                    "tool": tname,
                                    "description": tdesc,
                                    "parameters": tinfo.get("params", {}),
                                })
                    t_res = {"query": q, "tools": results}
                elif fn_name == "google_ai":
                    t_res = server_core.tools.google_ai_search(query=fn_args.get("query", ""))
                elif fn_name in ("view_image", "load_image", "inspect_image"):
                    t_res = await _execute_view_image_tool(
                        fn_args.get("image_path") or fn_args.get("path") or "",
                        fn_args.get("query") or "Describe this image in detail and transcribe all visible text accurately.",
                    )
                elif fn_name == "image_analysis":
                    if (fn_args.get("image_path") or fn_args.get("path")) and not fn_args.get("image_base64"):
                        t_res = await _execute_view_image_tool(
                            fn_args.get("image_path") or fn_args.get("path"),
                            fn_args.get("query") or "Describe this image",
                        )
                    else:
                        t_res = server_core.tools.analyze_image(**fn_args)
                elif fn_name == "code_execute":
                    t_res = server_core.tools.execute_code(**fn_args)
                elif fn_name == "research":
                    t_res = server_core.tools.research(**fn_args)
                elif fn_name in ("background_task", "coding_agent", "computer_use"):
                    target_dev = fn_args.get("target_device")
                    b_type = fn_args.get("task_type") or (
                        "coding" if "coding" in fn_name else ("computer_use" if "computer" in fn_name else "research")
                    )
                    fn_args["task_type"] = b_type
                    fn_args["origin"] = "server"
                    fn_args["caller_id"] = caller_device_id
                    fn_args["conversation_id"] = c_id
                    if (target_dev in ("pc", "pc_main", "local_pc", "tui", "terminal") or b_type in ("coding", "computer_use", "desktop_control", "gui_agent")) and device_registry.is_online("pc_main") and caller_device_id != "pc_main":
                        t_res = await device_registry.execute_tool_async("pc_main", "background_task", fn_args, caller_id=caller_device_id)
                    else:
                        try:
                            from App.core.TaskManager import dispatch_background_task
                            b_id, b_msg = dispatch_background_task(task_type=b_type, **fn_args)
                            t_res = {"status": "started" if b_id else "error", "task_id": b_id, "message": b_msg}
                        except Exception as e:
                            t_res = {"status": "error", "message": f"Could not launch background task: {e}"}
                elif fn_name == "get_background_tasks_status":
                    try:
                        from App.core.TaskManager import get_running_tasks_summary
                        t_res = {"status": "success", "summary": get_running_tasks_summary()}
                    except Exception as e:
                        t_res = {"status": "error", "message": str(e)}
                elif fn_name == "manage_background_tasks":
                    try:
                        from App.core.TaskManager import manage_background_tasks
                        act = fn_args.get("action", "list")
                        m_res = manage_background_tasks(action=act, **fn_args)
                        t_res = {"status": "success", "result": m_res}
                    except Exception as e:
                        t_res = {"status": "error", "message": str(e)}
                elif fn_name in ("conversation_history", "check_chat_history", "get_chat_history", "inspect_conversation", "search_conversations"):
                    t_res = server_core.conversations.inspect_or_search(
                        action=fn_args.get("action", "get"),
                        conv_id=fn_args.get("conversation_id"),
                        query=fn_args.get("query"),
                        limit=fn_args.get("limit", 20),
                        current_conv_id=payload.get("conversation_id", ""),
                    )
                elif fn_name == "memory_manager":
                    from services.memory import handle_memory_manager
                    t_res = handle_memory_manager(
                        action=fn_args.get("action", "retrieve"),
                        category=fn_args.get("category"),
                        key=fn_args.get("key"),
                        value=fn_args.get("value"),
                        search=fn_args.get("search"),
                    )
                elif server_tool_registry.has_tool(fn_name):
                    t_res = await server_tool_registry.execute_async(fn_name, fn_args)
                else:
                    t_res = {"error": f"Unknown tool: {fn_name}"}

                cur_messages.append({
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "name": fn_name,
                    "content": json.dumps(t_res, ensure_ascii=False) if not isinstance(t_res, str) else t_res,
                })

            result = await asyncio.to_thread(
                server_core.llm.generate,
                messages=cur_messages,
                tools=tools,
                context_message=context,
                model=model,
                model_tier=model_tier,
            )

        if all_tool_calls:
            result["tool_calls"] = all_tool_calls

    return result


@app.post("/generate/stream")
async def generate_stream_endpoint(request: Request):
    payload = await request.json()
    messages = payload.get("messages", [])
    tools = payload.get("tools") or get_all_server_tool_schemas()
    caller_device_id = payload.get("caller_device_id") or payload.get("caller_id")

    include_ctx = payload.get("include_server_context", True)
    if isinstance(include_ctx, str):
        include_ctx = include_ctx.strip().lower() not in ("0", "false", "no", "off")

    # Build system prompt with identity, tool routing, and behavior rules
    system_prompt = get_server_system_prompt()

    now_dt = datetime.datetime.now()
    clock_env = (
        f"[SYSTEM ENVIRONMENT & REAL-TIME CLOCK]\n"
        f"Current Time: {now_dt.strftime('%A, %B %d, %Y, %I:%M:%S %p')} ({time.tzname[0] if time.tzname else 'Local'})\n"
        f"Caller Device: {caller_device_id or 'web_client'}\n"
        f"Instruction: You already know the current real-world time, date, and day of the week from this clock. Answer time and date queries directly and immediately without calling device_time, unless the user specifically asks to check an external remote device."
    )
    context = f"{system_prompt}\n\n{clock_env}"

    c_id = payload.get("conversation_id") or ""
    if include_ctx:
        recent_ctx = server_core.conversations.get_recent_chats_context(
            current_conv_id=c_id,
            min_chats=5,
            updated_within_minutes=15,
        )
        if recent_ctx:
            context = f"{context}\n\n{recent_ctx}"

    presence_summary = device_registry.get_presence_summary(caller_id=caller_device_id)
    if presence_summary:
        context = f"{context}\n\n{presence_summary}".strip()

    model = payload.get("model")
    model_tier = payload.get("model_tier")

    async def _stream_generator():
        loop = asyncio.get_running_loop()
        queue = asyncio.Queue()

        def _sync_worker():
            try:
                for event in server_core.llm.generate_stream(
                    messages=messages,
                    tools=tools,
                    context_message=context,
                    model=model,
                    model_tier=model_tier,
                ):
                    loop.call_soon_threadsafe(queue.put_nowait, event)
            except Exception as e:
                loop.call_soon_threadsafe(queue.put_nowait, {"type": "error", "error": str(e)})
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        threading.Thread(target=_sync_worker, daemon=True).start()

        while True:
            item = await queue.get()
            if item is None:
                break
            if item.get("type") == "content":
                yield json.dumps({"type": "chunk", "delta": item.get("delta", "")}) + "\n"
            elif item.get("type") == "finish":
                yield json.dumps({"type": "final", "message": item.get("message", {})}) + "\n"
            elif item.get("type") == "error":
                yield json.dumps({"type": "error", "error": item.get("error", "")}) + "\n"

    return StreamingResponse(_stream_generator(), media_type="application/x-ndjson")


# ── WebSocket Hub (/ws/device) ────────────────────────────────────────────────

@app.websocket("/ws/device")
async def device_websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    current_device_id: Optional[str] = None

    try:
        while True:
            raw_text = await websocket.receive_text()
            try:
                data = json.loads(raw_text)
            except Exception:
                continue

            msg_type = data.get("type", "")

            # 1. Device Registration Handshake
            if msg_type == "register":
                d_id = str(data.get("device_id", "")).strip().lower()
                d_name = data.get("name") or d_id
                d_type = data.get("device_type", "web")
                caps = data.get("capabilities", [])
                catalog = data.get("tool_catalog", {})

                if d_id:
                    current_device_id = d_id
                    reg = device_registry.register(
                        device_id=d_id,
                        name=d_name,
                        device_type=d_type,
                        capabilities=caps,
                        tool_catalog=catalog,
                        ws=websocket,
                        metadata=data.get("metadata", {}),
                    )
                    await websocket.send_text(json.dumps({
                        "type": "registered",
                        "device_id": d_id,
                        "status": "online",
                        "presence": get_current_user_presence(),
                        "message": f"Device '{d_id}' registered successfully.",
                        "conversations": server_core.conversations.list_recent(limit=50),
                    }))

            # 2. Heartbeat & Ping
            elif msg_type in ("heartbeat", "ping"):
                if current_device_id:
                    device_registry.heartbeat(current_device_id)
                ack_type = "heartbeat_ack" if msg_type == "heartbeat" else "pong"
                await websocket.send_text(json.dumps({"type": ack_type, "timestamp": time.time()}))

            # 3. RPC Tool Response from Client
            elif msg_type == "tool_result":
                rpc_id = data.get("rpc_id")
                result = data.get("result")
                error = data.get("error")
                if rpc_id:
                    device_registry.handle_rpc_response(rpc_id, result=result, error=error)

            # 4. Chat Message from Client (Processed asynchronously so loop continues receiving tool_results)
            elif msg_type == "chat_message":
                asyncio.create_task(_handle_chat_message(data, websocket, current_device_id))

            # 5. Remote task completion ping from PC client
            elif msg_type in ("server_task_completed", "remote_task_completed"):
                asyncio.create_task(_handle_remote_task_completed(data))

            # 6. User Presence Update from client
            elif msg_type in ("user_presence", "presence_update"):
                new_p = str(data.get("presence", "present")).lower()
                set_current_user_presence(new_p)
                up_msg = {"type": "user_presence_updated", "presence": new_p}
                for dev in device_registry.list_devices(online_only=True):
                    d_id = dev.get("device_id")
                    if d_id:
                        asyncio.create_task(device_registry.send_to_device(d_id, up_msg))

    except WebSocketDisconnect:
        logger.info(f"[WebSocket] Device '{current_device_id}' disconnected")
    except OSError as e:
        # WinError 121 = semaphore timeout (common Windows network I/O issue)
        # WinError 10054 = connection reset by peer (client disconnected abruptly)
        win_err = getattr(e, "winerror", None)
        if win_err in (121, 10054, 10053, 10060):
            logger.info(f"[WebSocket] Device '{current_device_id}' connection lost (Windows network error {win_err})")
        else:
            logger.warning(f"[WebSocket] OSError on device socket '{current_device_id}': {e}")
    except Exception as e:
        logger.error(f"[WebSocket] Exception on device socket {current_device_id}: {e}")
    finally:
        device_registry.unregister_ws(websocket)


async def _run_layer2_delegation_turn(
    ticket_id: str,
    user_text: str,
    conv_id: str,
    caller_id: str,
    websocket: WebSocket,
    msgs: list,
) -> None:
    """
    Executes the full Layer 2 cognitive loop for delegated objectives:
    1. Runs real tool execution (e.g. multi-device queries, battery, web search, files) via generate_internal.
    2. Streams live thoughts & steps to Blackboard.
    3. Synthesizes the final output and dispatches it via WebSocket as the completed response.
    """
    from starlette.websockets import WebSocketState

    async def _safe_send(payload: dict) -> bool:
        try:
            if websocket and getattr(websocket, "client_state", None) == WebSocketState.CONNECTED:
                await websocket.send_text(json.dumps(payload))
                return True
        except Exception:
            pass
        return False

    try:
        blackboard.append_thought(
            ticket_id,
            f"Chief of Staff executing objective: '{user_text}'. Interrogating required tools and device nodes...",
            stage="orchestration",
        )

        # Execute target tool loop with full device registry and server tools
        gen_payload = {
            "messages": msgs,
            "caller_device_id": caller_id,
            "auto_resolve_tools": True,
            "ws": websocket,
            "conversation_id": conv_id,
        }

        gen_result = await generate_internal(gen_payload)
        final_text = (gen_result.get("content") or "").strip()

        if not final_text:
            if gen_result.get("tool_calls"):
                final_text = "All requested device operations and queries have completed successfully, sir."
            else:
                final_text = "Operation completed, sir."

        # Mark ticket completed on Blackboard
        # Mark ticket completed on Blackboard
        blackboard.update_ticket_status(
            ticket_id,
            status="completed",
            progress_pct=100,
            executive_summary=final_text[:120],
        )
        blackboard.append_thought(ticket_id, "Chief of Staff finalized outcome. Handing over to Layer 1 for delivery.", stage="completed")

        # Layer 1 Avatar delivers the outcome directly to the user
        delivered_text = layer1_avatar.deliver_delegation_outcome(
            user_goal=user_text,
            ticket_id=ticket_id,
            layer2_result=final_text,
            client_id=caller_id,
        )

        # Save to persistent conversation store as Layer 1 response
        server_core.conversations.add_message(
            role="assistant",
            content=delivered_text,
            conv_id=conv_id,
            client_id="ava_layer1",
        )

        # Stop thinking indicator
        await _safe_send({
            "type": "thinking",
            "status": "completed",
            "ticket_id": ticket_id,
        })

        # Send final assistant response to client via Layer 1
        await _safe_send({
            "type": "assistant_response",
            "conversation_id": conv_id,
            "content": delivered_text,
            "metadata": {
                "ticket_id": ticket_id,
                "cognitive_mode": "dual_layer_completed",
                "origin": "layer1",
                "sender": "layer1_avatar",
                "tool_calls": gen_result.get("tool_calls", []),
            },
        })

    except Exception as e:
        logger.error(f"[Layer2] Delegation execution failed for ticket {ticket_id}: {e}", exc_info=True)
        blackboard.update_ticket_status(ticket_id, status="failed", active_blocker=str(e))
        err_msg = f"Layer 2 encountered an error while completing the task: {e}"
        await _safe_send({
            "type": "thinking",
            "status": "completed",
            "ticket_id": ticket_id,
        })
        await _safe_send({
            "type": "assistant_response",
            "conversation_id": conv_id,
            "content": err_msg,
            "metadata": {"ticket_id": ticket_id, "cognitive_mode": "dual_layer_error"},
        })


async def _handle_chat_message(data: dict, websocket: WebSocket, current_device_id: Optional[str]) -> None:
    """Asynchronous worker for chat messages so the WebSocket receive loop stays free for RPC tool_results."""
    from starlette.websockets import WebSocketState

    user_text = str(data.get("message") or data.get("text") or data.get("content") or "").strip()
    conv_id = data.get("conversation_id")
    caller_id = current_device_id or data.get("device_id", "web_client")
    attachments = data.get("attachments") or []

    if not user_text and not attachments:
        return

    # Process attachments
    att_descriptions = []
    image_parts_for_turn = []
    for att in attachments:
        if isinstance(att, dict):
            fn = att.get("filename") or "file"
            sp = att.get("server_path") or att.get("relative_path") or fn
            is_img = att.get("is_image", False) or fn.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"))
            att_descriptions.append(f"[Attached {'Image' if is_img else 'Document'}: {fn} (Server Path: {sp})]")

            if is_img:
                cand_file = Path(sp)
                if not cand_file.is_file():
                    cand_file = UPLOADS_DIR / fn
                if cand_file.is_file():
                    try:
                        b = cand_file.read_bytes()
                        m = att.get("mime") or _guess_mime(fn)
                        b64 = base64.b64encode(b).decode("ascii")
                        image_parts_for_turn.append({
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{m};base64,{b64}",
                                "detail": "high",
                            },
                        })
                    except Exception as ex:
                        logger.warning(f"Failed to encode attachment {fn} for vision: {ex}")

    if att_descriptions:
        att_block = "\n".join(att_descriptions)
        full_user_text = f"{user_text}\n\n{att_block}".strip() if user_text else att_block
    else:
        full_user_text = user_text

    async def _safe_send(payload: dict) -> bool:
        """Send a WS message only if the socket is still open. Returns False if closed."""
        try:
            if websocket.client_state == WebSocketState.CONNECTED:
                await websocket.send_text(json.dumps(payload))
                return True
        except Exception:
            pass
        return False

    # Track tool steps for this turn
    turn_tool_steps: list[dict[str, Any]] = []

    try:
        # Append message to server conversation store
        c_id = server_core.conversations.add_message(
            role="user",
            content=full_user_text,
            conv_id=conv_id,
            client_id=caller_id,
        )

        # Get thread history
        thread = server_core.conversations.get_thread(c_id)
        all_msgs = thread.get("messages", []) if thread else [{"role": "user", "content": full_user_text}]
        msgs = list(all_msgs[-12:] if len(all_msgs) > 12 else all_msgs)

        # If we have image parts for the current turn, equip the last user message with multimodal content
        if image_parts_for_turn and msgs and msgs[-1].get("role") == "user":
            msgs[-1] = {
                "role": "user",
                "content": [
                    {"type": "text", "text": full_user_text},
                    *image_parts_for_turn,
                ],
            }

        # Check Dual-Layer mode
        if get_settings().dual_layer_enabled and not image_parts_for_turn:
            dual_res = await layer1_avatar.route_turn(
                user_text=user_text,
                client_id=caller_id,
                conversation_id=c_id,
                conversation_history=msgs[:-1] if msgs else None,
            )
            res_type = dual_res.get("type")

            if res_type == "status":
                reply_text = dual_res.get("response", "")
                server_core.conversations.add_message(
                    role="assistant",
                    content=reply_text,
                    conv_id=c_id,
                    client_id=caller_id,
                )
                await _safe_send({
                    "type": "assistant_response",
                    "conversation_id": c_id,
                    "content": reply_text,
                    "metadata": {"cognitive_mode": "dual_layer_status"},
                })
                return

            elif res_type == "reflex":
                reply_text = dual_res.get("response", "")
                server_core.conversations.add_message(
                    role="assistant",
                    content=reply_text,
                    conv_id=c_id,
                    client_id=caller_id,
                )
                await _safe_send({
                    "type": "assistant_response",
                    "conversation_id": c_id,
                    "content": reply_text,
                    "metadata": {"cognitive_mode": "dual_layer_reflex"},
                })
                return

            elif res_type == "delegation":
                kickoff_text = dual_res.get("response", "")
                ticket_id = dual_res.get("ticket_id")

                # Step 1: Immediate Layer 1 natural spoken/text kickoff acknowledgment (<300ms)
                server_core.conversations.add_message(
                    role="assistant",
                    content=kickoff_text,
                    conv_id=c_id,
                    client_id=caller_id,
                )
                await _safe_send({
                    "type": "assistant_response",
                    "conversation_id": c_id,
                    "content": kickoff_text,
                    "metadata": {"ticket_id": ticket_id, "cognitive_mode": "dual_layer_kickoff"},
                })

                # Step 2: Open thinking drawer showing Chief of Staff orchestration
                await _safe_send({
                    "type": "thinking",
                    "status": "start",
                    "label": f"Layer 2 Chief of Staff executing [{ticket_id}]...",
                    "ticket_id": ticket_id,
                })

                # Step 3: Launch Layer 2 asynchronous tool execution and synthesis
                asyncio.create_task(
                    _run_layer2_delegation_turn(
                        ticket_id=ticket_id,
                        user_text=user_text,
                        conv_id=c_id,
                        caller_id=caller_id,
                        websocket=websocket,
                        msgs=msgs,
                    )
                )
                return

        # Use direct LLM call
        await _safe_send({"type": "thinking", "status": "start"})
        gen_payload = {
            "messages": msgs,
            "caller_device_id": caller_id,
            "auto_resolve_tools": True,
            "ws": websocket,
            "conversation_id": c_id,
        }

        gen_result = await generate_internal(gen_payload)
        assistant_text = gen_result.get("content")
        if not assistant_text or not str(assistant_text).strip():
            if gen_result.get("tool_calls"):
                assistant_text = "I've checked the conversation history and retrieved the details."
            else:
                assistant_text = "I'm here. How can I assist you?"

        # Extract tool steps from agent_step messages sent during this turn
        # We reconstruct from tool_calls and their results
        tool_calls = gen_result.get("tool_calls", [])
        for tc in tool_calls:
            fn = tc.get("function", {})
            step = {
                "tool": fn.get("name", "unknown"),
                "arguments": json.loads(fn.get("arguments", "{}")) if isinstance(fn.get("arguments"), str) else (fn.get("arguments") or {}),
                "status": "completed",
            }
            turn_tool_steps.append(step)

        server_core.conversations.add_message(
            role="assistant",
            content=assistant_text,
            tool_calls=tool_calls,
            tool_steps=turn_tool_steps if turn_tool_steps else None,
            conv_id=c_id,
            client_id=caller_id,
        )

        await _safe_send({"type": "thinking", "status": "stop"})
        await _safe_send({
            "type": "assistant_response",
            "conversation_id": c_id,
            "content": assistant_text,
            "tool_calls": tool_calls,
        })
    except Exception as e:
        logger.error(f"[ChatHandler] Error processing message: {e}", exc_info=True)
        await _safe_send({"type": "thinking", "status": "stop"})
        await _safe_send({"type": "error", "message": str(e)})
        target_conv = locals().get("c_id") or conv_id or "default"
        error_msg = f"⚠️ Encountered an error: {e}"
        try:
            server_core.conversations.add_message(
                role="assistant",
                content=error_msg,
                conv_id=target_conv,
                client_id=caller_id,
            )
        except Exception:
            pass
        await _safe_send({
            "type": "assistant_response",
            "conversation_id": target_conv,
            "content": error_msg,
            "tool_calls": [],
        })


async def _broadcast_tasks_update() -> None:
    try:
        from App.core.TaskManager import get_registry
        registry = get_registry()
        running = registry.get_running_tasks()
        tasks_list = [_format_task_payload(t) for t in running]
        payload = {
            "type": "tasks_updated",
            "tasks": tasks_list,
            "count": len(tasks_list),
        }
        for dev in device_registry.list_devices(online_only=True):
            d_id = dev.get("device_id")
            if d_id != "pc_main":
                await device_registry.send_to_device(d_id, payload)
    except Exception as ex:
        logger.warning(f"[ServerTaskReporter] Failed to broadcast tasks: {ex}")


async def _synthesize_and_dispatch_task_completion(
    conv_id: str | None,
    caller_id: str | None,
    task_id: str,
    task_type: str,
    task_name: str,
    status: str,
    raw_result: str,
    artifact_path: str | None = None,
) -> None:
    """Notify the Agent with full context and dispatch conversational response to user."""
    await _broadcast_tasks_update()

    if not conv_id and server_core:
        conv_id = server_core.conversations.current_conversation_id or ""
        if not conv_id and server_core.conversations.index:
            conv_id = next(iter(server_core.conversations.index.keys()), "")
        if not conv_id:
            conv_id = server_core.conversations.start(client_id=caller_id or "default")

    artifact_text = ""
    if artifact_path and os.path.exists(artifact_path):
        try:
            with open(artifact_path, "r", encoding="utf-8", errors="replace") as af:
                artifact_text = af.read()
        except Exception as e:
            logger.warning(f"[ServerTaskReporter] Error reading artifact {artifact_path}: {e}")

    context_chunks = []
    if raw_result and raw_result.strip():
        context_chunks.append(f"Result Output:\n{raw_result.strip()}")
    if artifact_text and artifact_text.strip() and artifact_text.strip() != raw_result.strip():
        context_chunks.append(f"Full Report / Artifact ({artifact_path}):\n{artifact_text.strip()}")
    if not context_chunks:
        context_chunks.append(f"Status: {status}")

    full_context = "\n\n".join(context_chunks)
    if len(full_context) > 25000:
        full_context = full_context[:25000] + "\n... [Context truncated for length]"

    agent_instruction = (
        f"[System Notice: Background Task Completed]\n"
        f"Task ID: {task_id}\n"
        f"Task Type: {task_type}\n"
        f"Topic / Objective: {task_name}\n"
        f"Status: {status}\n\n"
        f"--- TASK FINDINGS & CONTEXT ---\n"
        f"{full_context}\n"
        f"-------------------------------\n\n"
        f"INSTRUCTION FOR AVA: Your background task has concluded. As the AI assistant AVA, directly and naturally inform the user about the results. "
        f"Provide a clear, engaging breakdown of what was discovered or accomplished, citing key findings, data, and conclusions. "
        f"Do NOT say '[System Notice]' or sound like an internal event log. Speak conversationally as AVA delivering the finished work to the user.\n"
        f"CRITICAL FILE LOCATION RULE: AVA Server is a backend service. Any generated artifact or report files reside in the server's central archive, NOT directly on the user's PC or Desktop. "
        f"NEVER tell the user the file is saved on their PC or Desktop unless you explicitly transferred it using the 'save_file' tool on the PC Client ('pc_main'). "
        f"If the user asks to save to PC or if 'pc_main' is offline, state clearly that the file is safely stored in the server archive and explain that 'pc_main' is offline, so you can send it to their PC as soon as the PC client connects."
    )

    thread = server_core.conversations.get_thread(conv_id) if (conv_id and server_core) else None
    raw_msgs = []
    if isinstance(thread, dict):
        raw_msgs = thread.get("messages", [])
    elif hasattr(thread, "to_llm_messages"):
        raw_msgs = thread.to_llm_messages()

    history_messages = []
    for m in raw_msgs:
        if isinstance(m, dict) and m.get("role") in ("user", "assistant"):
            history_messages.append({
                "role": m.get("role"),
                "content": m.get("content") or "",
            })

    agent_messages = list(history_messages[-8:])
    agent_messages.append({
        "role": "user",
        "content": agent_instruction,
    })

    try:
        agent_reply_dict = await asyncio.to_thread(
            server_core.llm.generate,
            messages=agent_messages,
            tools=[],
            context_message="",
        )
        content = agent_reply_dict.get("content") or (
            f"The {task_type} task (ID: `{task_id}`) has finished:\n\n{raw_result}"
        )
    except Exception as ex:
        logger.error(f"[ServerTaskReporter] LLM generation error on task completion: {ex}", exc_info=True)
        content = f"Your {task_type} task (ID: `{task_id}`) has finished:\n\n{raw_result or 'Task completed successfully.'}"

    if conv_id and server_core:
        try:
            server_core.conversations.add_message(
                role="assistant",
                content=content,
                conv_id=conv_id,
                client_id="server_agent",
            )
            server_core.conversations.save_current()
        except Exception as e:
            logger.warning(f"[ServerTaskReporter] Failed to record task completion in conv: {e}")

    payload = {
        "type": "assistant_response",
        "conversation_id": conv_id,
        "content": content,
        "metadata": {
            "task_id": task_id,
            "task_type": task_type,
            "origin": "background_task_completed",
            "artifact_path": artifact_path or "",
        }
    }

    target_devices = set()
    if caller_id and caller_id != "pc_main":
        target_devices.add(caller_id)
    for dev in device_registry.list_devices(online_only=True):
        d_id = dev.get("device_id")
        if d_id and d_id != "pc_main":
            target_devices.add(d_id)

    print(f"[ServerTaskReporter] Dispatching agent task report to {list(target_devices)} for conv={conv_id}", flush=True)
    for d_id in target_devices:
        ok = await device_registry.send_to_device(d_id, payload)
        print(f"[ServerTaskReporter] Pushed agent task report to '{d_id}' (success={ok})", flush=True)


async def _handle_remote_task_completed(data: dict) -> None:
    """Handle task completion ping pushed from PC for a task initiated by the server."""
    conv_id = data.get("conversation_id")
    caller_id = data.get("caller_id")
    task_id = data.get("task_id", "")
    task_type = data.get("task_type", "coding")
    status = data.get("status", "completed")
    summary = data.get("summary") or ""
    error = data.get("error") or ""
    raw = summary if status == "completed" else (error or "Task execution failed")
    task_name = data.get("task_name") or f"{task_type.capitalize()} on PC"

    await _synthesize_and_dispatch_task_completion(
        conv_id=conv_id,
        caller_id=caller_id,
        task_id=task_id,
        task_type=task_type,
        task_name=task_name,
        status=status,
        raw_result=raw,
        artifact_path=data.get("artifact_path"),
    )


_handled_completed_tasks: set[str] = set()


async def _handle_local_task_completed(task) -> None:
    """Handle task completion from server-side TaskManager."""
    t_id = getattr(task, "id", "")
    if not t_id or t_id in _handled_completed_tasks:
        return
    _handled_completed_tasks.add(t_id)
    if len(_handled_completed_tasks) > 2000:
        _handled_completed_tasks.clear()

    print(f"[ServerTaskReporter] _handle_local_task_completed triggered for task {t_id} (status={getattr(task, 'status', '')}, conv={getattr(task, 'conversation_id', '')})", flush=True)
    conv_id = getattr(task, "conversation_id", None)
    meta = getattr(task, "metadata", {}) or {}
    caller_id = meta.get("caller_id")
    task_name = getattr(task, "name", None) or getattr(task, "original_query", None) or f"{getattr(task, 'task_type', 'task')} task"
    raw_status = getattr(task, "status", "completed")
    status_str = raw_status.value if hasattr(raw_status, "value") else str(raw_status)
    raw = str(getattr(task, "result", None) or getattr(task, "error", None) or "")

    await _synthesize_and_dispatch_task_completion(
        conv_id=conv_id,
        caller_id=caller_id,
        task_id=getattr(task, "id", ""),
        task_type=getattr(task, "task_type", "general"),
        task_name=task_name,
        status=status_str,
        raw_result=raw,
        artifact_path=getattr(task, "artifact_path", None),
    )



def _format_step_label(fn_name: str, fn_args: dict) -> str:
    """Format human-readable activity label for live UI status pills (LanguageGUI style)."""
    if fn_name == "google_ai":
        q = str(fn_args.get("query", "")).strip()
        return f"Searching web for '{q[:40]}...'" if len(q) > 40 else (f"Searching web for '{q}'" if q else "Searching the web...")
    elif fn_name == "device_location":
        tgt = fn_args.get("target_device", "caller")
        return f"Checking location of {tgt}..."
    elif fn_name == "device_battery":
        tgt = fn_args.get("target_device", "caller")
        return f"Checking battery of {tgt}..."
    elif fn_name == "device_ring":
        tgt = fn_args.get("target_device", "caller")
        return f"Ringing device {tgt}..."
    elif fn_name == "device_music":
        act = fn_args.get("action", "music")
        return f"Controlling audio playback ({act})..."
    elif fn_name in ("device_time", "timedate"):
        return "Reading local time..."
    elif fn_name == "code_execute":
        return "Executing code snippet..."
    elif fn_name == "image_analysis":
        return "Analyzing image..."
    elif fn_name == "research":
        return "Conducting research..."
    elif fn_name == "background_task":
        tt = fn_args.get("task_type", "task")
        top = fn_args.get("topic") or fn_args.get("objective") or ""
        return f"Starting background {tt}..." if not top else f"Starting background {tt}: {top[:30]}..."
    elif fn_name == "get_background_tasks_status":
        return "Checking background task status..."
    elif fn_name == "manage_background_tasks":
        act = fn_args.get("action", "manage")
        return f"Managing background tasks ({act})..."
    elif fn_name in ("conversation_history", "check_chat_history", "get_chat_history", "inspect_conversation", "search_conversations"):
        act = fn_args.get("action", "check")
        return f"Checking conversation history ({act})..."
    elif fn_name == "use_skill":
        s_name = fn_args.get("name", "skill")
        return f"Loading skill '{s_name}'..."
    elif fn_name == "list_skills":
        return "Scanning available skills..."
    elif "__" in fn_name:
        server_prefix, _, t_sub = fn_name.partition("__")
        return f"Running MCP [{server_prefix}] {t_sub}..."
    elif server_tool_registry.has_tool(fn_name):
        clean_name = fn_name.replace("_", " ").title()
        return f"Running {clean_name}..."
    return f"Executing {fn_name}..."


async def generate_internal(payload: dict) -> dict:
    """Helper for internal generation calls from websocket handler with clock injection and instant short-circuits."""
    from starlette.websockets import WebSocketState

    messages = payload.get("messages", [])
    tools = get_all_server_tool_schemas()
    caller_device_id = payload.get("caller_device_id")
    conv_id = payload.get("conversation_id") or ""
    ws = payload.get("ws")

    def _ws_ok() -> bool:
        return ws is not None and getattr(ws, "client_state", None) == WebSocketState.CONNECTED

    async def _step(payload_dict: dict) -> None:
        if _ws_ok():
            try:
                await ws.send_text(json.dumps(payload_dict))
            except Exception:
                pass

    # Build system prompt with identity, tool routing, and behavior rules
    system_prompt = get_server_system_prompt()

    now_dt = datetime.datetime.now()
    clock_env = (
        f"[SYSTEM ENVIRONMENT & REAL-TIME CLOCK]\n"
        f"Current Time: {now_dt.strftime('%A, %B %d, %Y, %I:%M:%S %p')} ({time.tzname[0] if time.tzname else 'Local'})\n"
        f"Caller Device: {caller_device_id or 'web_client'}\n"
        f"Instruction: You already know the current real-world time, date, and day of the week from this clock. Answer time and date queries directly and immediately without calling device_time, unless the user specifically asks to check an external remote device."
    )
    context = f"{system_prompt}\n\n{clock_env}"

    recent_ctx = server_core.conversations.get_recent_chats_context(
        current_conv_id=conv_id,
        min_chats=5,
        updated_within_minutes=15,
    )
    if recent_ctx:
        context = f"{context}\n\n{recent_ctx}"

    presence = device_registry.get_presence_summary(caller_id=caller_device_id)
    if presence:
        context = f"{context}\n\n{presence}".strip()

    # Inject long-term + agent-saved memories (dreamer store + agent_memories.json)
    try:
        from services.memory import get_memory_context
        mem_ctx = get_memory_context()
        if mem_ctx:
            context = f"{context}\n\n{mem_ctx}"
    except Exception as e:
        logger.warning(f"[Memory] Context injection failed: {e}")

    async def _generate_with_streaming(target_msgs: list, active_tools: list | None = None) -> dict:
        use_tools = tools if active_tools is None else active_tools
        if _ws_ok():
            loop = asyncio.get_running_loop()
            q = asyncio.Queue()

            def _worker():
                try:
                    for ev in server_core.llm.generate_stream(
                        messages=target_msgs,
                        tools=use_tools,
                        context_message=context,
                    ):
                        loop.call_soon_threadsafe(q.put_nowait, ev)
                except Exception as ex:
                    loop.call_soon_threadsafe(q.put_nowait, {"type": "error", "error": str(ex)})
                finally:
                    loop.call_soon_threadsafe(q.put_nowait, None)

            threading.Thread(target=_worker, daemon=True).start()
            final_res = None
            while True:
                item = await q.get()
                if item is None:
                    break
                if item.get("type") == "content":
                    await _step({"type": "assistant_chunk", "delta": item.get("delta", "")})
                elif item.get("type") == "finish":
                    final_res = item.get("message", {})
                elif item.get("type") == "error":
                    final_res = {
                        "role": "assistant",
                        "content": f"[AVA System Note] LLM error: {item.get('error')}",
                        "tool_calls": [],
                    }
            return final_res or {"role": "assistant", "content": "", "tool_calls": []}
        else:
            return await asyncio.to_thread(
                server_core.llm.generate,
                messages=target_msgs,
                tools=use_tools,
                context_message=context,
            )

    try:
        result = await _generate_with_streaming(messages)
    except Exception as e:
        logger.error(f"[LLM] Error generating internal response: {e}")
        return {
            "role": "assistant",
            "content": f"[AVA System Note] LLM unreachable: {e}",
            "tool_calls": [],
        }

    all_tool_calls = []
    if result.get("tool_calls"):
        cur_messages = list(messages)
        rounds = 0
        while result.get("tool_calls") and rounds < 5:
            rounds += 1
            launched_bg_task = False
            all_tool_calls.extend(result["tool_calls"])
            cur_messages.append({
                "role": "assistant",
                "content": result.get("content"),
                "tool_calls": result.get("tool_calls"),
            })
            for tc in result["tool_calls"]:
                fn = tc.get("function", {})
                fn_name = fn.get("name")
                fn_args = json.loads(fn.get("arguments", "{}")) if isinstance(fn.get("arguments"), str) else (fn.get("arguments") or {})
                tc_id = tc.get("id", f"call_{rounds}")

                step_label = _format_step_label(fn_name, fn_args)
                await _step({
                    "type": "agent_step",
                    "status": "running",
                    "tool": fn_name,
                    "label": step_label,
                    "arguments": fn_args,
                })

                if fn_name == "device_location":
                    target = fn_args.get("target_device", "caller")
                    t_res = await device_registry.execute_tool_async(target, "get_location", fn_args, caller_id=caller_device_id)
                    if t_res.get("status") == "error":
                        pc_loc = None
                        try:
                            from App.core.device_client import _get_pc_location
                            pc_loc = _get_pc_location()
                        except Exception as e:
                            logger.warning(f"Could not import device_client._get_pc_location: {e}")
                        if not pc_loc:
                            pc_loc = {
                                "latitude": 28.5526,
                                "longitude": 77.5540,
                                "city": "Greater Noida",
                                "region": "Uttar Pradesh",
                                "country": "India",
                                "address": "Dadri, Greater Noida, Uttar Pradesh, 201314, India",
                                "accuracy_meters": 2000,
                                "provider": "network_ip"
                            }
                        t_res = {
                            "status": "success",
                            "device_id": target,
                            "tool": "get_location",
                            "result": pc_loc,
                        }
                elif fn_name == "device_battery":
                    target = fn_args.get("target_device", "caller")
                    t_res = await device_registry.execute_tool_async(target, "get_battery_status", fn_args, caller_id=caller_device_id)
                elif fn_name == "device_ring":
                    target = fn_args.get("target_device", "caller")
                    t_res = await device_registry.execute_tool_async(target, "ring_device", fn_args, caller_id=caller_device_id)
                elif fn_name == "device_music":
                    target = fn_args.get("target_device", "caller")
                    t_res = await device_registry.execute_tool_async(target, "music", fn_args, caller_id=caller_device_id)
                elif fn_name in ("device_time", "device_timedate", "timedate"):
                    target = fn_args.get("target_device", "caller")
                    if target in ("caller", "current", "me", "this", "here", "local", "") or (caller_device_id and str(target).strip().lower() == str(caller_device_id).strip().lower()):
                        now = datetime.datetime.now()
                        t_res = {
                            "status": "success",
                            "device_id": caller_device_id or "local",
                            "tool": "timedate",
                            "result": {
                                "local_time": now.strftime("%I:%M:%S %p"),
                                "local_date": now.strftime("%Y-%m-%d"),
                                "timezone": time.tzname[0] if time.tzname else "Local",
                            },
                        }
                    else:
                        t_res = await device_registry.execute_tool_async(
                            target, "timedate", fn_args, caller_id=caller_device_id, timeout=1.8
                        )
                        if t_res.get("status") == "error":
                            now = datetime.datetime.now()
                            t_res = {
                                "status": "success",
                                "device_id": "system_clock",
                                "tool": "timedate",
                                "result": {
                                    "local_time": now.strftime("%I:%M:%S %p"),
                                    "local_date": now.strftime("%Y-%m-%d"),
                                    "timezone": "System Local Time",
                                },
                            }
                elif fn_name == "device_tool":
                    target = fn_args.get("target_device", "caller")
                    t_name = fn_args.get("tool_name", "")
                    sub_args = fn_args.get("arguments")
                    if not isinstance(sub_args, dict):
                        sub_args = {k: v for k, v in fn_args.items() if k not in ("target_device", "tool_name")}
                    if t_name in ("timedate", "time", "get_time") and (target in ("caller", "current", "me", "this", "here", "local", "") or (caller_device_id and str(target).strip().lower() == str(caller_device_id).strip().lower())):
                        now = datetime.datetime.now()
                        t_res = {
                            "status": "success",
                            "device_id": caller_device_id or "local",
                            "tool": "timedate",
                            "result": {
                                "local_time": now.strftime("%I:%M:%S %p"),
                                "local_date": now.strftime("%Y-%m-%d"),
                                "timezone": time.tzname[0] if time.tzname else "Local",
                            },
                        }
                    else:
                        t_res = await device_registry.execute_tool_async(target, t_name, sub_args, caller_id=caller_device_id)
                        if t_res.get("status") == "error" and t_name in ("timedate", "time", "get_time"):
                            now = datetime.datetime.now()
                            t_res = {
                                "status": "success",
                                "device_id": "system_clock",
                                "tool": "timedate",
                                "result": {
                                    "local_time": now.strftime("%I:%M:%S %p"),
                                    "local_date": now.strftime("%Y-%m-%d"),
                                    "timezone": "System Local Time",
                                },
                            }
                elif fn_name == "google_ai":
                    t_res = server_core.tools.google_ai_search(query=fn_args.get("query", ""))
                elif fn_name in ("view_image", "load_image", "inspect_image"):
                    t_res = await _execute_view_image_tool(
                        fn_args.get("image_path") or fn_args.get("path") or "",
                        fn_args.get("query") or "Describe this image in detail and transcribe all visible text accurately.",
                    )
                elif fn_name == "image_analysis":
                    if (fn_args.get("image_path") or fn_args.get("path")) and not fn_args.get("image_base64"):
                        t_res = await _execute_view_image_tool(
                            fn_args.get("image_path") or fn_args.get("path"),
                            fn_args.get("query") or "Describe this image",
                        )
                    else:
                        t_res = server_core.tools.analyze_image(**fn_args)
                elif fn_name == "code_execute":
                    t_res = server_core.tools.execute_code(**fn_args)
                elif fn_name == "research":
                    t_res = server_core.tools.research(**fn_args)
                elif fn_name in ("store_screenshot", "store_image"):
                    t_res = server_core.tools.store_screenshot(**fn_args)
                elif fn_name == "tool_search":
                    q = fn_args.get("query", "")
                    t_dev = fn_args.get("target_device")
                    results = device_registry.search_tools(q, t_dev)
                    if not t_dev or t_dev in ("server", "local", "all"):
                        for tname, tinfo in server_tool_registry.get_tools().items():
                            tdesc = tinfo.get("description", "")
                            if not q or q.lower() in tname.lower() or q.lower() in tdesc.lower():
                                results.append({
                                    "device_id": "server",
                                    "device_name": "AVA Server",
                                    "online": True,
                                    "tool": tname,
                                    "description": tdesc,
                                    "parameters": tinfo.get("params", {}),
                                })
                    t_res = {"query": q, "tools": results}
                elif fn_name in ("background_task", "coding_agent", "computer_use"):
                    target_dev = fn_args.get("target_device")
                    b_type = fn_args.get("task_type") or (
                        "coding" if "coding" in fn_name else ("computer_use" if "computer" in fn_name else "research")
                    )
                    fn_args["task_type"] = b_type
                    fn_args["origin"] = "server"
                    fn_args["caller_id"] = caller_device_id
                    fn_args["conversation_id"] = conv_id
                    if (target_dev in ("pc", "pc_main", "local_pc", "tui", "terminal") or b_type in ("coding", "computer_use", "desktop_control", "gui_agent")) and device_registry.is_online("pc_main") and caller_device_id != "pc_main":
                        t_res = await device_registry.execute_tool_async("pc_main", "background_task", fn_args, caller_id=caller_device_id)
                        if isinstance(t_res, dict) and t_res.get("status") in ("started", "ok", "success"):
                            launched_bg_task = True
                    else:
                        try:
                            from App.core.TaskManager import dispatch_background_task
                            task_dispatch_args = dict(fn_args)
                            task_dispatch_args.pop("task_type", None)
                            b_id, b_msg = dispatch_background_task(task_type=b_type, **task_dispatch_args)
                            t_res = {
                                "status": "started" if b_id else "error",
                                "task_id": b_id,
                                "message": b_msg or f"{b_type.capitalize()} background task successfully started.",
                                "instruction": "The task has been dispatched and is running in the background. DO NOT call synchronous research, coding, or execution tools. Immediately reply to the user confirming that the task has started and that AVA will notify them once it finishes.",
                            }
                            if t_res.get("status") == "started":
                                launched_bg_task = True
                        except Exception as e:
                            t_res = {"status": "error", "message": f"Could not launch background task: {e}"}
                elif fn_name == "get_background_tasks_status":
                    try:
                        from App.core.TaskManager import get_running_tasks_summary
                        t_res = {"status": "success", "summary": get_running_tasks_summary()}
                    except Exception as e:
                        t_res = {"status": "error", "message": str(e)}
                elif fn_name == "manage_background_tasks":
                    try:
                        from App.core.TaskManager import manage_background_tasks
                        act = fn_args.get("action", "list")
                        m_res = manage_background_tasks(action=act, **fn_args)
                        t_res = {"status": "success", "result": m_res}
                    except Exception as e:
                        t_res = {"status": "error", "message": str(e)}
                elif fn_name in ("conversation_history", "check_chat_history", "get_chat_history", "inspect_conversation", "search_conversations"):
                    t_res = server_core.conversations.inspect_or_search(
                        action=fn_args.get("action", "get"),
                        conv_id=fn_args.get("conversation_id"),
                        query=fn_args.get("query"),
                        limit=fn_args.get("limit", 20),
                        current_conv_id=conv_id,
                    )
                elif fn_name == "memory_manager":
                    from services.memory import handle_memory_manager
                    t_res = handle_memory_manager(
                        action=fn_args.get("action", "retrieve"),
                        category=fn_args.get("category"),
                        key=fn_args.get("key"),
                        value=fn_args.get("value"),
                        search=fn_args.get("search"),
                    )
                elif server_tool_registry.has_tool(fn_name):
                    t_res = await server_tool_registry.execute_async(fn_name, fn_args)
                else:
                    t_res = {"error": f"Tool {fn_name} not resolved"}

                await _step({
                    "type": "agent_step",
                    "status": "completed",
                    "tool": fn_name,
                    "label": step_label.rstrip("."),
                    "arguments": fn_args,
                    "result": t_res,
                })

                cur_messages.append({
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "name": fn_name,
                    "content": json.dumps(t_res, ensure_ascii=False) if not isinstance(t_res, str) else t_res,
                })

            if launched_bg_task:
                await _step({
                    "type": "thinking",
                    "status": "start",
                    "label": "Confirming background task...",
                })
                result = await _generate_with_streaming(cur_messages, active_tools=[])
                break

            await _step({
                "type": "thinking",
                "status": "start",
                "label": "Synthesizing answer...",
            })

            result = await _generate_with_streaming(cur_messages)

        if all_tool_calls:
            result["tool_calls"] = all_tool_calls

    return result


async def _run_servers_async(host: str, http_port: int, https_port: int, cert_file: str, key_file: str) -> None:
    import contextlib

    config_http = uvicorn.Config(
        app,
        host=host,
        port=http_port,
        log_level="info",
        access_log=False,
        ws_ping_interval=30.0,
        ws_ping_timeout=30.0,
    )
    server_http = uvicorn.Server(config_http)
    tasks = [server_http.serve()]

    if os.path.exists(cert_file) and os.path.exists(key_file):
        config_https = uvicorn.Config(
            app,
            host=host,
            port=https_port,
            ssl_certfile=cert_file,
            ssl_keyfile=key_file,
            log_level="info",
            access_log=False,
            ws_ping_interval=30.0,
            ws_ping_timeout=30.0,
        )
        server_https = uvicorn.Server(config_https)
        # Avoid signal capture collision with primary HTTP server
        server_https.capture_signals = contextlib.nullcontext
        tasks.append(server_https.serve())
        logger.info(f"Secure HTTPS/WSS Portal running on https://{host}:{https_port}")

    logger.info(f"Standard HTTP/WS Portal running on http://{host}:{http_port}")
    await asyncio.gather(*tasks)


def _install_signal_handlers() -> None:
    """Ensure Ctrl+C / SIGINT terminates the process cleanly."""
    import signal

    def _shutdown(signum, frame):
        print("\n[AVA-SERVER] Received shutdown signal, exiting...")
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)


def run_server() -> None:
    _install_signal_handlers()
    settings = get_settings()
    cert_file = str(SERVER_DIR / "certs" / "cert.pem")
    key_file = str(SERVER_DIR / "certs" / "key.pem")
    https_port = int(os.getenv("AVA_SERVER_HTTPS_PORT", "8766"))

    if os.getenv("AVA_DUAL_SERVER", "1") == "1" and os.path.exists(cert_file) and os.path.exists(key_file):
        try:
            asyncio.run(_run_servers_async(settings.host, settings.port, https_port, cert_file, key_file))
            return
        except KeyboardInterrupt:
            return
        except Exception as e:
            import traceback
            traceback.print_exc()
            logger.error(f"Fatal error in _run_servers_async: {e}", exc_info=True)
            return

    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_level="info",
        access_log=False,
        ws_ping_interval=30.0,
        ws_ping_timeout=30.0,
    )


if __name__ == "__main__":
    run_server()
