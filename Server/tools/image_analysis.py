"""Image analysis via Groq Vision."""

from typing import Any
from groq import Groq


class ImageAnalysis:
    def __init__(self, api_key: str):
        self.client = Groq(api_key=api_key)

    def execute(self, image_base64: str, query: str) -> dict[str, Any]:
        try:
            result = self.client.chat.completions.create(
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": query},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}},
                    ],
                }],
                model="meta-llama/llama-4-scout-17b-16e-instruct",
            )
            return {"status": "success", "content": result.choices[0].message.content}
        except Exception as e:
            return {"status": "error", "content": str(e)}