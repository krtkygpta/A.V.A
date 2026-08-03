"""Tool Service - Thin wrapper for server-side tool execution."""

import os
import sys
from pathlib import Path
from typing import Any, Optional

# Add Server directory to path for tools import
SERVER_DIR = Path(__file__).resolve().parent.parent
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from tools import GoogleAISearch, ImageAnalysis, CodeSandbox, ResearchAgent


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
        self.image_analysis = ImageAnalysis(api_key=groq_api_key)
        self.code_sandbox = CodeSandbox()
        self.research_agent = ResearchAgent(
            tavily_key=tavily_api_key,
            groq_key=groq_api_key,
            openai_api_key=openai_api_key or os.getenv("OPENAI_API_KEY"),
            openai_base_url=openai_base_url or os.getenv("OPENAI_BASE_URL"),
            model_name=model_name or os.getenv("MODEL_NAME"),
        )

    def google_ai_search(self, query: str) -> dict[str, Any]:
        return self.google_ai.execute(query=query)

    def analyze_image(self, image_base64: str, query: str) -> dict[str, Any]:
        return self.image_analysis.execute(image_base64=image_base64, query=query)

    def execute_code(self, code: str, timeout: int = 5) -> dict[str, Any]:
        return self.code_sandbox.execute(code=code, timeout=timeout)

    def research(self, question: str, level: int = 3) -> dict[str, Any]:
        return self.research_agent.execute(question=question, level=level)
