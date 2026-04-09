import os
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


def generate_remote_response(messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
    return _post_json(
        "/generate",
        payload={"messages": messages, "tools": tools},
        timeout=max(DEFAULT_TIMEOUT, 45.0),
    )


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


def add_remote_message(role: str, content: str, tool_id: str = "") -> bool:
    try:
        _post_json(
            "/conversation/message",
            payload={
                "role": role,
                "content": content,
                "tool_call_id": tool_id or "",
            },
        )
        return True
    except Exception:
        return False


def save_remote_conversation() -> bool:
    try:
        _post_json("/conversation/save")
        return True
    except Exception:
        return False


def list_remote_conversations(limit: int = 10) -> list[dict[str, Any]]:
    try:
        payload = _post_json("/conversation/list", payload={"limit": limit})
        return payload.get("conversations", [])
    except Exception:
        return []
