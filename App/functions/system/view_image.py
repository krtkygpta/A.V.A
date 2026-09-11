"""
View image tool for the main agent.

Flow:
  1. Load an image from a local path on the client PC (or download from a URL)
  2. Upload image bytes to the AVA server (stored under Server/data/screenshots)
  3. Server returns its absolute path
  4. Tool returns multimodal content (text with server path + image_url data URL)
     so the vision-capable AI model can inspect the raw image and reference its path
"""

from __future__ import annotations

import base64
import mimetypes
import os
from pathlib import Path
from typing import Any, List, Optional, Union
import urllib.parse


def _image_bytes_to_data_url(data: bytes, mime: str = "image/png") -> str:
    encoded = base64.b64encode(data).decode("utf-8")
    return f"data:{mime};base64,{encoded}"


def _guess_mime(path_or_url: str, default: str = "image/png") -> str:
    clean = path_or_url.split("?")[0].split("#")[0]
    mime, _ = mimetypes.guess_type(clean)
    if mime:
        return mime
    ext = Path(clean).suffix.lower()
    mapping = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".bmp": "image/bmp",
        ".svg": "image/svg+xml",
        ".tiff": "image/tiff",
        ".tif": "image/tiff",
    }
    return mapping.get(ext, default)


def load_image_bytes(image_path: str) -> tuple[bytes, str, str]:
    """
    Read an image from local filesystem or URL.

    Returns (image_bytes, filename, mime_type).
    """
    raw_path = str(image_path or "").strip().strip('"').strip("'")
    if not raw_path:
        raise ValueError("image_path is required")

    # Handle web URL
    if raw_path.startswith(("http://", "https://")):
        import requests

        resp = requests.get(raw_path, timeout=25)
        resp.raise_for_status()
        data = resp.content
        if not data:
            raise ValueError(f"Downloaded empty image content from {raw_path}")

        parsed = urllib.parse.urlparse(raw_path)
        name = Path(parsed.path).name or "downloaded_image.png"
        mime = resp.headers.get("Content-Type") or _guess_mime(name)
        if ";" in mime:
            mime = mime.split(";", 1)[0].strip()
        return data, name, mime

    # Handle file:// URL
    if raw_path.startswith("file://"):
        parsed = urllib.parse.urlparse(raw_path)
        file_path_str = urllib.parse.unquote(parsed.path)
        # Windows file:///C:/path adjustment
        if os.name == "nt" and file_path_str.startswith("/") and len(file_path_str) > 2 and file_path_str[2] == ":":
            file_path_str = file_path_str[1:]
        local_path = Path(file_path_str)
    else:
        # Expand ~ and environment variables
        expanded = os.path.expanduser(os.path.expandvars(raw_path))
        local_path = Path(expanded)

    if not local_path.is_absolute():
        local_path = local_path.resolve()

    if not local_path.exists():
        raise FileNotFoundError(f"Image file does not exist: {local_path}")

    if not local_path.is_file():
        raise ValueError(f"Path is not a regular file: {local_path}")

    data = local_path.read_bytes()
    if not data:
        raise ValueError(f"Image file is empty (0 bytes): {local_path}")

    mime = _guess_mime(str(local_path))
    if not mime.startswith("image/"):
        raise ValueError(
            f"File '{local_path.name}' has MIME type '{mime}', which is not a supported image format. "
            "To read a PDF, spreadsheet, or document, use document reading tools or code execution."
        )
    name = local_path.name
    return data, name, mime


def view_image(
    image_path: str,
    filename: Optional[str] = None,
) -> Union[List[dict[str, Any]], str]:
    """
    Main-agent tool: load image from client PC/URL and return base64 image_url for vision analysis.

    Success → multimodal tool content:
        [
          {"type": "text", "text": "Loaded image: filename.png (path: C:/...)"},
          {"type": "image_url", "image_url": {"url": "data:image/png;base64,...", "detail": "high"}}
        ]

    Failure → plain error string.
    """
    try:
        data, default_name, mime = load_image_bytes(image_path)
        name = str(filename).strip().strip('"').strip("'") if filename else default_name
        data_url = _image_bytes_to_data_url(data, mime=mime)

        return [
            {
                "type": "text",
                "text": f"Loaded image '{name}' from {image_path}",
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
        return f"Failed to load image '{image_path}': {exc}"

