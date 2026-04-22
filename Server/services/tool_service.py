"""Tool Service - Thin wrapper for server-side tool execution."""

import sys
from pathlib import Path
from typing import Any

# Add Server directory to path for tools import
SERVER_DIR = Path(__file__).resolve().parent.parent
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from tools import GoogleAISearch, ImageAnalysis, CodeSandbox, ResearchAgent


class ToolService:
    """Service for executing tools on the server."""

    def __init__(self, groq_api_key: str, google_ai_api_key: str, tavily_api_key: str):
        self.google_ai = GoogleAISearch(api_key=google_ai_api_key)
        self.image_analysis = ImageAnalysis(api_key=groq_api_key)
        self.code_sandbox = CodeSandbox()
        self.research_agent = ResearchAgent(groq_key=groq_api_key, tavily_key=tavily_api_key)

    def google_ai_search(self, query: str) -> dict[str, Any]:
        return self.google_ai.execute(query=query)

    def analyze_image(self, image_base64: str, query: str) -> dict[str, Any]:
        return self.image_analysis.execute(image_base64=image_base64, query=query)

    def execute_code(self, code: str, timeout: int = 5) -> dict[str, Any]:
        return self.code_sandbox.execute(code=code, timeout=timeout)

    def research(self, question: str) -> dict[str, Any]:
        return self.research_agent.execute(question=question)