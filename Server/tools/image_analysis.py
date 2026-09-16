"""Image analysis via Google GenAI (Gemini Vision) or Groq."""

import base64
from typing import Any, Optional


class ImageAnalysis:
    def __init__(self, google_ai_api_key: Optional[str] = None, groq_api_key: Optional[str] = None):
        self.google_client = None
        self.groq_client = None

        if google_ai_api_key:
            try:
                from google import genai

                self.google_client = genai.Client(api_key=google_ai_api_key)
            except Exception:
                pass

        if groq_api_key:
            try:
                from groq import Groq

                self.groq_client = Groq(api_key=groq_api_key)
            except Exception:
                pass

    def execute(
        self,
        image_base64: Optional[str] = None,
        query: str = "Describe what is shown in this image in detail and transcribe all visible text accurately.",
        image_path: Optional[str] = None,
    ) -> dict[str, Any]:
        data = None
        mime = "image/png"

        # 1. Try reading from image_path if provided
        if image_path:
            p = str(image_path).strip().strip('"').strip("'")
            cand_paths = [
                Path(p),
                Path(__file__).resolve().parent.parent / "data" / "uploads" / Path(p).name,
                Path(__file__).resolve().parent.parent / p,
            ]
            for cand in cand_paths:
                try:
                    if cand.is_file():
                        data = cand.read_bytes()
                        ext = cand.suffix.lower()
                        if ext in (".jpg", ".jpeg"):
                            mime = "image/jpeg"
                        elif ext == ".webp":
                            mime = "image/webp"
                        elif ext == ".gif":
                            mime = "image/gif"
                        break
                except Exception:
                    continue

        # 2. If no path or path reading failed, use image_base64
        if data is None:
            raw = (image_base64 or "").strip()
            if not raw:
                return {"status": "error", "content": "Missing image data or path"}

            if raw.startswith("data:") and "," in raw:
                header, raw = raw.split(",", 1)
                if ";base64" in header:
                    mime = header.split(";", 1)[0].removeprefix("data:") or "image/png"

            try:
                data = base64.b64decode(raw, validate=False)
            except Exception as e:
                return {"status": "error", "content": f"Invalid base64: {e}"}

        # 1. Try Google Gemini Vision (Best quality OCR & visual understanding)
        if self.google_client:
            try:
                from google.genai import types

                resp = self.google_client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=[
                        types.Part.from_bytes(data=data, mime_type=mime),
                        query
                        or "Describe what is shown in this image in detail and transcribe all visible text accurately.",
                    ],
                )
                if resp and resp.text:
                    return {"status": "success", "content": resp.text.strip()}
            except Exception as e:
                pass

        return {"status": "error", "content": "Vision analysis service unavailable"}