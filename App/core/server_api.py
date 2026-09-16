import os
from pathlib import Path
from typing import Any, Optional

import requests


SERVER_URL = os.getenv("AVA_SERVER_URL", "http://127.0.0.1:8765").rstrip("/")
DEFAULT_TIMEOUT = float(os.getenv("AVA_SERVER_TIMEOUT", "4"))


def _post_json(path: str, payload: Optional[dict[str, Any]] = None, timeout: Optional[float] = None) -> dict[str, Any]:
    response = requests.post(
        f"{SERVER_URL}{path}",
        json=payload or {},
        timeout=timeout or DEFAULT_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def generate_remote_response(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    *,
    model_tier: Optional[str] = None,
    model: Optional[str] = None,
    include_server_context: bool = True,
    timeout: Optional[float] = None,
) -> dict[str, Any]:
    """
    Call server /generate.

    model_tier: none|nano|small|main|heavy — server maps to a concrete model.
    model: optional explicit model id (overrides tier mapping).
    include_server_context: when False, skip recent_hour_context (workers).
    """
    payload: dict[str, Any] = {
        "messages": messages,
        "tools": tools,
        "include_server_context": bool(include_server_context),
        "caller_device_id": os.getenv("AVA_CLIENT_ID", "pc_main"),
        "auto_resolve_tools": False,
    }
    if model_tier:
        payload["model_tier"] = model_tier
    if model:
        payload["model"] = model
    return _post_json(
        "/generate",
        payload=payload,
        timeout=timeout if timeout is not None else max(DEFAULT_TIMEOUT, 45.0),
    )


def generate_remote_response_stream(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    *,
    model_tier: Optional[str] = None,
    model: Optional[str] = None,
    include_server_context: bool = True,
):
    """
    Call server /generate/stream (NDJSON streaming).
    Yields ("chunk", delta_str) and finally ("final", message_dict).
    Falls back to non-streaming generate_remote_response if streaming fails.
    """
    import json
    payload: dict[str, Any] = {
        "messages": messages,
        "tools": tools,
        "include_server_context": bool(include_server_context),
        "caller_device_id": os.getenv("AVA_CLIENT_ID", "pc_main"),
        "auto_resolve_tools": False,
    }
    if model_tier:
        payload["model_tier"] = model_tier
    if model:
        payload["model"] = model

    try:
        response = requests.post(
            f"{SERVER_URL}/generate/stream",
            json=payload,
            stream=True,
            timeout=max(DEFAULT_TIMEOUT, 45.0),
        )
        response.raise_for_status()

        final_msg = None
        for line in response.iter_lines(decode_unicode=True):
            if not line:
                continue
            try:
                data = json.loads(line)
            except Exception:
                continue

            event_type = data.get("type")
            if event_type == "chunk":
                delta = data.get("delta", "")
                if delta:
                    yield ("chunk", delta)
            elif event_type == "final":
                final_msg = data.get("message")
            elif event_type == "error":
                raise RuntimeError(data.get("error", "Stream error"))

        if final_msg is not None:
            yield ("final", final_msg)
            return

    except Exception as e:
        print(f"[server_api] Streaming fallback to non-streaming generate: {e}")
        fallback_msg = generate_remote_response(
            messages=messages,
            tools=tools,
            model_tier=model_tier,
            model=model,
            include_server_context=include_server_context,
        )
        content = fallback_msg.get("content")
        if content:
            yield ("chunk", content)
        yield ("final", fallback_msg)


def synthesize_remote_tts(text: str) -> bytes:
    response = requests.post(
        f"{SERVER_URL}/tts",
        json={"text": text},
        timeout=max(DEFAULT_TIMEOUT, 45.0),
    )
    response.raise_for_status()
    return response.content


def start_remote_conversation() -> Optional[str]:
    try:
        payload = _post_json("/conversation/start")
        return payload.get("conversation_id")
    except Exception:
        return None


import threading


def add_remote_message(role: str, content: str, tool_id: str = "") -> bool:
    """Send message to remote conversation store asynchronously to avoid blocking UI."""
    def _bg():
        try:
            _post_json(
                "/conversation/message",
                payload={
                    "role": role,
                    "content": content,
                    "tool_call_id": tool_id or "",
                },
                timeout=3.0,
            )
        except Exception:
            pass

    threading.Thread(target=_bg, daemon=True).start()
    return True


def save_remote_conversation() -> bool:
    """Trigger remote conversation save in background."""
    def _bg():
        try:
            _post_json("/conversation/save", timeout=3.0)
        except Exception:
            pass

    threading.Thread(target=_bg, daemon=True).start()
    return True


def list_remote_conversations(limit: int = 50) -> list[dict[str, Any]]:
    try:
        payload = _post_json("/conversation/list", payload={"limit": limit})
        return payload.get("conversations", [])
    except Exception:
        return []


def get_remote_conversation_history(conv_id: str) -> list[dict[str, Any]]:
    """Fetch full message history for a conversation from the server."""
    try:
        payload = _post_json("/conversation/history", payload={"id": conv_id})
        return payload.get("messages", [])
    except Exception:
        try:
            res = requests.get(f"{SERVER_URL}/conversation/history?id={conv_id}", timeout=DEFAULT_TIMEOUT)
            if res.ok:
                return res.json().get("messages", [])
        except Exception:
            pass
        return []


def sync_remote_conversation(conv_dict: dict[str, Any]) -> bool:
    """Push a full conversation thread to the server to synchronize."""
    try:
        _post_json("/conversation/sync", payload={"conversation": conv_dict})
        return True
    except Exception:
        return False


def store_screenshot_remote(
    image_base64: str,
    filename: Optional[str] = None,
    mime: Optional[str] = None,
) -> dict[str, Any]:
    """
    Upload a captured screenshot to the AVA server.

    Server stores under Server/data/screenshots and returns its absolute path
    (on the server machine), so client/server need not share a filesystem.
    """
    payload: dict[str, Any] = {"image_base64": image_base64}
    if filename:
        payload["filename"] = filename
    if mime:
        payload["mime"] = mime
    return _post_json(
        "/tools/store_screenshot",
        payload=payload,
        timeout=max(DEFAULT_TIMEOUT, 30.0),
    )


def upload_file_remote(file_path: str | Path) -> Optional[dict[str, Any]]:
    """
    Upload a single local file to the AVA server via /api/upload.
    Returns the file info dict (including 'server_path', 'url', 'filename', etc.) or None.
    """
    from pathlib import Path
    p = Path(file_path)
    if not p.is_file():
        return None
    try:
        with open(p, "rb") as f:
            files = [("files", (p.name, f))]
            res = requests.post(f"{SERVER_URL}/api/upload", files=files, timeout=max(DEFAULT_TIMEOUT, 30.0))
            if res.ok:
                data = res.json()
                if data.get("ok") and data.get("files"):
                    return data["files"][0]
    except Exception as exc:
        print(f"[server_api] Upload error: {exc}")
    return None
