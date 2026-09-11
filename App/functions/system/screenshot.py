"""
Screenshot tool for the main agent.

Flow:
  1. Capture the desktop on the client
  2. Upload image bytes to the AVA server
  3. Server stores under Server/data/screenshots and returns that absolute path
  4. Tool returns multimodal content (text with server path + image_url data URL)
     so the main agent can both reference the path and see the image
"""

from __future__ import annotations

import base64
import io
import mimetypes
from pathlib import Path
from typing import Any, List, Optional, Union


def _image_bytes_to_data_url(data: bytes, mime: str = "image/png") -> str:
    encoded = base64.b64encode(data).decode("utf-8")
    return f"data:{mime};base64,{encoded}"


def _image_to_data_url_from_path(path: Path | str) -> str:
    p = Path(path)
    mime, _ = mimetypes.guess_type(str(p))
    if mime is None:
        mime = "image/png"
    with open(p, "rb") as f:
        return _image_bytes_to_data_url(f.read(), mime=mime)


def capture_screenshot_bytes(filename: Optional[str] = None) -> tuple[bytes, str, str]:
    """
    Capture the screen as PNG bytes.

    Returns (png_bytes, suggested_filename, mime).
    """
    try:
        import pyautogui
    except ImportError as exc:
        raise RuntimeError(
            "pyautogui is required for screenshots. Install with: pip install pyautogui"
        ) from exc

    if filename:
        name = str(filename).strip().strip('"').strip("'")
        name = Path(name).name
        if not name.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp")):
            name = f"{name}.png"
    else:
        from datetime import datetime

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        name = f"screenshot_{stamp}.png"

    image = pyautogui.screenshot()
    buf = io.BytesIO()
    # Always encode as PNG for upload (lossless UI capture)
    image.save(buf, format="PNG")
    data = buf.getvalue()
    if not data:
        raise RuntimeError("Screenshot capture produced empty image data")

    # Force .png extension to match bytes
    stem = Path(name).stem or "screenshot"
    name = f"{stem}.png"
    return data, name, "image/png"


def screenshot(
    filename: Optional[str] = None,
) -> Union[List[dict[str, Any]], str]:
    """
    Main-agent tool: capture screen → store on server → return path + image.

    Success → multimodal tool content (like test.py):
        [
          {"type": "text", "text": "Screenshot: name.png (SERVER_ABS_PATH)"},
          {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}
        ]

    Failure → plain error string.
    """
    try:
        data, name, mime = capture_screenshot_bytes(filename=filename)
        b64 = base64.b64encode(data).decode("utf-8")

        from core.server_api import store_screenshot_remote

        result = store_screenshot_remote(
            image_base64=b64,
            filename=name,
            mime=mime,
        )

        if not isinstance(result, dict) or result.get("status") != "success":
            err = (
                (result or {}).get("content")
                or (result or {}).get("error")
                or "unknown server error"
            )
            return f"Failed to store screenshot on server: {err}"

        server_path = result.get("path") or ""
        if not server_path:
            return "Server stored screenshot but returned no path"

        return [
            {
                "type": "text",
                "text": f"Screenshot: {Path(server_path).name} ({server_path})",
            },
            {
                "type": "image_url",
                "image_url": {
                    "url": _image_bytes_to_data_url(data, mime=mime),
                    "detail": "high",
                },
            },
        ]
    except Exception as exc:
        return f"Failed to capture screenshot: {exc}"
