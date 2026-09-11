"""Tool Service - Thin wrapper for server-side tool execution."""

import base64
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

# Add Server directory to path for tools import
SERVER_DIR = Path(__file__).resolve().parent.parent
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from tools import GoogleAISearch, ImageAnalysis, CodeSandbox, ResearchAgent

# Server/data/screenshots — canonical store for client-uploaded captures
SCREENSHOTS_DIR = SERVER_DIR / "data" / "screenshots"


class ToolService:
    """Service for executing tools on the server."""

    def __init__(
        self,
        groq_api_key: str,
        google_ai_api_key: str,
        tavily_api_key: str,
        *,
        openai_api_key: Optional[str] = None,
        openai_base_url: Optional[str] = None,
        model_name: Optional[str] = None,
    ):
        self.google_ai = GoogleAISearch(api_key=google_ai_api_key)
        self.image_analysis = ImageAnalysis(
            google_ai_api_key=google_ai_api_key,
            groq_api_key=groq_api_key,
        )
        self.code_sandbox = CodeSandbox()
        self.research_agent = ResearchAgent(
            tavily_key=tavily_api_key,
            groq_key=groq_api_key,
            openai_api_key=openai_api_key or os.getenv("OPENAI_API_KEY"),
            openai_base_url=openai_base_url or os.getenv("OPENAI_BASE_URL"),
            model_name=model_name or os.getenv("MODEL_NAME"),
        )
        SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

    def google_ai_search(self, query: str) -> dict[str, Any]:
        return self.google_ai.execute(query=query)

    def analyze_image(self, image_base64: str, query: str) -> dict[str, Any]:
        return self.image_analysis.execute(image_base64=image_base64, query=query)

    def execute_code(self, code: str, timeout: int = 5) -> dict[str, Any]:
        return self.code_sandbox.execute(code=code, timeout=timeout)

    def research(self, question: str, level: int = 3) -> dict[str, Any]:
        return self.research_agent.execute(question=question, level=level)

    def store_screenshot(
        self,
        image_base64: str,
        filename: Optional[str] = None,
        mime: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Save a client-captured screenshot under Server/data/screenshots.

        Returns absolute path on the server machine so later server-side tools
        (and remote clients without a shared FS) can reference it.
        """
        raw = (image_base64 or "").strip()
        if not raw:
            return {"status": "error", "content": "Missing image_base64"}

        # Accept full data URLs or bare base64
        if raw.startswith("data:") and "," in raw:
            header, raw = raw.split(",", 1)
            if not mime and ";base64" in header:
                # data:image/png;base64
                mime = header.split(";", 1)[0].removeprefix("data:") or None

        try:
            data = base64.b64decode(raw, validate=False)
        except Exception as exc:
            return {"status": "error", "content": f"Invalid base64 image: {exc}"}

        if not data:
            return {"status": "error", "content": "Empty image payload"}

        ext = ".png"
        mime_l = (mime or "").lower()
        if "jpeg" in mime_l or "jpg" in mime_l:
            ext = ".jpg"
        elif "webp" in mime_l:
            ext = ".webp"
        elif "gif" in mime_l:
            ext = ".gif"
        elif "bmp" in mime_l:
            ext = ".bmp"
        elif "tiff" in mime_l or "tif" in mime_l:
            ext = ".tiff"
        elif "svg" in mime_l:
            ext = ".svg"

        valid_exts = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff", ".tif", ".svg")
        if filename:
            name = Path(str(filename).strip().strip('"').strip("'")).name
            name = re.sub(r"[^\w.\-]+", "_", name)
            if not name.lower().endswith(valid_exts):
                name = f"{name}{ext}"
        else:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            name = f"image_{stamp}{ext}"

        SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
        path = (SCREENSHOTS_DIR / name).resolve()
        try:
            path.write_bytes(data)
        except OSError as exc:
            return {"status": "error", "content": f"Failed to write image: {exc}"}

        if not path.is_file() or path.stat().st_size <= 0:
            return {"status": "error", "content": "Image was not written correctly"}

        abs_path = str(path)
        return {
            "status": "success",
            "path": abs_path,
            "filename": path.name,
            "content": f"Image stored at {abs_path}",
        }

    def store_image(
        self,
        image_base64: str,
        filename: Optional[str] = None,
        mime: Optional[str] = None,
    ) -> dict[str, Any]:
        """Alias for store_screenshot to store any client-provided image."""
        return self.store_screenshot(
            image_base64=image_base64,
            filename=filename,
            mime=mime,
        )

