from __future__ import annotations

import atexit
import json
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from config import ROOT_DIR, get_settings
from services import ConversationStore, LLMService, ToolService, TTSService
from services.token_usage import get_usage_summary

TOOL_SCHEMAS = [
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
            "name": "image_analysis",
            "description": "Analyze an image using Groq Vision",
            "parameters": {
                "type": "object",
                "properties": {
                    "image_base64": {
                        "type": "string",
                        "description": "Base64-encoded image",
                    },
                    "query": {
                        "type": "string",
                        "description": "Question about the image",
                    },
                },
                "required": ["image_base64"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "code_execute",
            "description": "Execute Python code in a sandbox",
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
]


@dataclass
class Response:
    status: int
    content_type: str
    body: bytes

    @classmethod
    def json(cls, payload: dict[str, Any], status: int = 200) -> "Response":
        return cls(
            status=status,
            content_type="application/json; charset=utf-8",
            body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        )

    @classmethod
    def wav(cls, wav_bytes: bytes, status: int = 200) -> "Response":
        return cls(status=status, content_type="audio/wav", body=wav_bytes)


class AvaServer:
    """HTTP API server for generation, TTS, and server-side conversation storage."""

    def __init__(self) -> None:
        settings = get_settings()
        self.host = settings.host
        self.port = settings.port

        self.conversations = ConversationStore(settings.conversation_dir)
        self.llm = LLMService(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            model_name=settings.model_name,
            api_keys=list(settings.openai_api_keys)
            if getattr(settings, "openai_api_keys", None)
            else None,
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

        # Route table is intentionally explicit so adding endpoints is easy.
        self.routes: dict[tuple[str, str], Callable[[dict[str, Any]], Response]] = {
            ("GET", "/health"): self.health,
            ("GET", "/memories"): self.memories,
            ("GET", "/usage"): self.usage,
            ("GET", "/token_usage"): self.usage,
            ("POST", "/health"): self.health,
            ("POST", "/generate"): self.generate,
            ("POST", "/tts"): self.tts_endpoint,
            ("POST", "/conversation/start"): self.conversation_start,
            ("POST", "/conversation/message"): self.conversation_message,
            ("POST", "/conversation/save"): self.conversation_save,
            ("POST", "/conversation/list"): self.conversation_list,
            ("POST", "/usage"): self.usage,
            ("POST", "/token_usage"): self.usage,
            # Tool endpoints
            ("POST", "/tools/google_ai"): self.tools_google_ai,
            ("POST", "/tools/image_analysis"): self.tools_image_analysis,
            ("POST", "/tools/code_execute"): self.tools_code_execute,
            ("POST", "/tools/research"): self.tools_research,
            ("POST", "/tools/store_screenshot"): self.tools_store_screenshot,
            # Additional endpoints
            ("POST", "/stt"): self.stt_endpoint,
            ("POST", "/tools/schema"): self.tools_schema,
            ("POST", "/tools/execute"): self.tools_execute,
        }

    def startup(self) -> None:
        if not self.tts.start():
            raise RuntimeError("Unable to start local Piper TTS engine on server.")

    def shutdown(self) -> None:
        self.tts.stop()

    def dispatch(self, method: str, path: str, payload: dict[str, Any]) -> Response:
        route = self.routes.get((method, path))
        if route is None:
            return Response.json({"error": "Not found"}, status=404)
        try:
            return route(payload)
        except Exception as exc:
            import traceback

            traceback.print_exc()
            return Response.json({"error": str(exc)}, status=500)

    # Endpoints -----------------------------------------------------------------
    def memories(self, _: dict[str, Any]) -> Response:
        """Return dreamer long-term store for system-prompt injection."""
        mem_dir = ROOT_DIR / "Server" / "data" / "memories"
        mem_path = mem_dir / "memories.txt"
        beh_path = mem_dir / "behaviours.txt"
        memories = mem_path.read_text(encoding="utf-8") if mem_path.exists() else ""
        behaviours = beh_path.read_text(encoding="utf-8") if beh_path.exists() else ""
        return Response.json({"memories": memories, "behaviours": behaviours})

    def health(self, _: dict[str, Any]) -> Response:
        return Response.json({"status": "ok"})

    def usage(self, payload: dict[str, Any]) -> Response:
        """Daily token usage totals, broken down per model (and label)."""
        day = None
        if isinstance(payload, dict):
            day = payload.get("date") or payload.get("day")
        # GET query support via path is limited; body/query both optional.
        if day is not None:
            day = str(day).strip() or None
        try:
            days_limit = int(payload.get("days_limit", 14)) if payload else 14
        except (TypeError, ValueError):
            days_limit = 14
        summary = get_usage_summary(day=day, include_days=True, days_limit=days_limit)
        return Response.json(summary)

    def generate(self, payload: dict[str, Any]) -> Response:
        messages = payload.get("messages", [])
        tools = payload.get("tools", [])
        # Workers set include_server_context=false to skip hour-context (tokens).
        include_ctx = payload.get("include_server_context", True)
        if isinstance(include_ctx, str):
            include_ctx = include_ctx.strip().lower() not in ("0", "false", "no", "off")
        context = self.conversations.recent_hour_context() if include_ctx else ""
        model = payload.get("model")
        model_tier = payload.get("model_tier")
        result = self.llm.generate(
            messages=messages,
            tools=tools,
            context_message=context,
            model=model,
            model_tier=model_tier,
        )
        return Response.json(result)

    def tts_endpoint(self, payload: dict[str, Any]) -> Response:
        text = str(payload.get("text", "")).strip()
        if not text:
            return Response.json({"error": "Missing 'text'."}, status=400)

        wav_bytes = self.tts.synthesize_bytes(text)
        if wav_bytes is None:
            return Response.json({"error": "Failed to synthesize audio."}, status=500)
        return Response.wav(wav_bytes)

    def conversation_start(self, _: dict[str, Any]) -> Response:
        conversation_id = self.conversations.start()
        return Response.json({"ok": True, "conversation_id": conversation_id})

    def conversation_message(self, payload: dict[str, Any]) -> Response:
        role = str(payload.get("role", "user"))
        content = str(payload.get("content", ""))
        tool_call_id = payload.get("tool_call_id")
        conversation_id = self.conversations.add_message(
            role=role,
            content=content,
            tool_call_id=str(tool_call_id) if tool_call_id else None,
        )
        return Response.json({"ok": True, "conversation_id": conversation_id})

    def conversation_save(self, _: dict[str, Any]) -> Response:
        self.conversations.save_current()
        return Response.json({"ok": True})

    def conversation_list(self, payload: dict[str, Any]) -> Response:
        limit = int(payload.get("limit", 10))
        return Response.json(
            {"conversations": self.conversations.list_recent(limit=limit)}
        )

    # STT endpoint --------------------------------------------------------------

    def stt_endpoint(self, payload: dict[str, Any]) -> Response:
        """Speech-to-text endpoint. Takes base64-encoded audio WAV data."""
        audio_base64 = str(payload.get("audio", "")).strip()
        if not audio_base64:
            return Response.json({"error": "Missing 'audio'."}, status=400)
        # TODO: Integrate real STT via Vosk or cloud API
        # For now, return a mock response.
        return Response.json({"text": "Mock transcription of audio", "confidence": 0.9})

    # Tool schema endpoint -------------------------------------------------------

    def tools_schema(self, _: dict[str, Any]) -> Response:
        """Returns OpenAI tool schemas for all available server-side tools."""
        return Response.json({"tools": TOOL_SCHEMAS})

    # Tool execute endpoint ------------------------------------------------------

    def tools_execute(self, payload: dict[str, Any]) -> Response:
        """Routes a tool call by name to the appropriate handler."""
        name = str(payload.get("name", "")).strip()
        arguments = payload.get("arguments", {})

        if not name:
            return Response.json({"error": "Missing 'name'."}, status=400)

        if name == "google_ai":
            return self.tools_google_ai(arguments)
        elif name == "image_analysis":
            return self.tools_image_analysis(arguments)
        elif name == "code_execute":
            return self.tools_code_execute(arguments)
        elif name == "research":
            return self.tools_research(arguments)
        elif name == "store_screenshot":
            return self.tools_store_screenshot(arguments)
        else:
            return Response.json({"error": f"Unknown tool: {name}"}, status=400)

    # Tool endpoints ------------------------------------------------------------

    def tools_google_ai(self, payload: dict[str, Any]) -> Response:
        """Google AI search with grounding."""
        query = str(payload.get("query", "")).strip()
        if not query:
            return Response.json({"error": "Missing 'query'."}, status=400)
        result = self.tools.google_ai_search(query=query)
        return Response.json(result)

    def tools_image_analysis(self, payload: dict[str, Any]) -> Response:
        """Analyze image via Groq Vision."""
        image_base64 = str(payload.get("image_base64", "")).strip()
        query = str(payload.get("query", "Describe this image")).strip()
        if not image_base64:
            return Response.json({"error": "Missing 'image_base64'."}, status=400)
        result = self.tools.analyze_image(image_base64=image_base64, query=query)
        return Response.json(result)

    def tools_code_execute(self, payload: dict[str, Any]) -> Response:
        """Execute Python code in sandbox."""
        code = str(payload.get("code", "")).strip()
        timeout = int(payload.get("timeout", 5))
        if not code:
            return Response.json({"error": "Missing 'code'."}, status=400)
        result = self.tools.execute_code(code=code, timeout=timeout)
        return Response.json(result)

    def tools_research(self, payload: dict[str, Any]) -> Response:
        """Multi-level research agent (levels 1–5)."""
        question = str(payload.get("question", "")).strip()
        if not question:
            return Response.json({"error": "Missing 'question'."}, status=400)
        try:
            level = int(payload.get("level", 3))
        except (TypeError, ValueError):
            level = 3
        level = max(1, min(5, level))
        result = self.tools.research(question=question, level=level)
        return Response.json(result)

    def tools_store_screenshot(self, payload: dict[str, Any]) -> Response:
        """Store a client-captured screenshot under Server/data/screenshots."""
        image_base64 = str(payload.get("image_base64", "")).strip()
        if not image_base64:
            return Response.json({"error": "Missing 'image_base64'."}, status=400)
        filename = payload.get("filename")
        mime = payload.get("mime")
        result = self.tools.store_screenshot(
            image_base64=image_base64,
            filename=str(filename) if filename else None,
            mime=str(mime) if mime else None,
        )
        status = 200 if result.get("status") == "success" else 400
        return Response.json(result, status=status)


def _read_json_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    content_length = int(handler.headers.get("Content-Length", "0"))
    raw = handler.rfile.read(content_length) if content_length else b"{}"
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        return {}


def _write_response(handler: BaseHTTPRequestHandler, response: Response) -> None:
    handler.send_response(response.status)
    handler.send_header("Content-Type", response.content_type)
    handler.send_header("Content-Length", str(len(response.body)))
    handler.end_headers()
    handler.wfile.write(response.body)


def run_server() -> None:
    # Prevent App-side mirroring logic when server imports shared modules later.
    # (Kept for compatibility if future shared imports are added.)
    import os

    os.environ["AVA_SERVER_MODE"] = "1"

    app = AvaServer()
    app.startup()
    atexit.register(app.shutdown)

    class RequestHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: Any) -> None:
            print(f"[AVA-SERVER] {self.address_string()} - {format % args}")

        def _handle(self, method: str) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            payload = _read_json_body(self) if method == "POST" else {}
            # Merge query string into payload for GET usage endpoints (date=YYYY-MM-DD).
            if method == "GET" and parsed.query:
                from urllib.parse import parse_qs

                qs = parse_qs(parsed.query)
                for key, values in qs.items():
                    if not values:
                        continue
                    if key not in payload or payload.get(key) in (None, ""):
                        payload[key] = values[0]
            response = app.dispatch(method=method, path=path, payload=payload)
            _write_response(self, response)

        def do_GET(self) -> None:
            self._handle("GET")

        def do_POST(self) -> None:
            self._handle("POST")

    httpd = ThreadingHTTPServer((app.host, app.port), RequestHandler)
    print(f"[AVA-SERVER] Listening on http://{app.host}:{app.port}")

    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()
        app.shutdown()
