"""Google AI Search with grounding."""

from typing import Any
from google import genai
from google.genai.types import Tool, GenerateContentConfig, GoogleSearch


class GoogleAISearch:
    def __init__(self, api_key: str):
        self.client = genai.Client(api_key=api_key)

    def execute(self, query: str) -> dict[str, Any]:
        try:
            response = self.client.models.generate_content(
                model="gemma-4-26b-a4b-it",
                contents=query,
                config=GenerateContentConfig(
                    tools=[Tool(google_search=GoogleSearch())],
                    response_modalities=["TEXT"],
                    max_output_tokens=2048,
                ),
            )
            content = " ".join(p.text for p in response.candidates[0].content.parts)
            return {"status": "success", "content": content}
        except Exception as e:
            return {"status": "error", "content": str(e)}