from __future__ import annotations

from typing import Any

from memorySystem.memoryDreamer import dreamerEvent
from openai import OpenAI


class LLMService:
    """Thin wrapper around chat completion to keep endpoint code clean."""

    def __init__(self, api_key: str | None, base_url: str | None, model_name: str):
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model_name = model_name

    @staticmethod
    def _valid_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        valid_tools: list[dict[str, Any]] = []
        for tool in tools or []:
            if (
                isinstance(tool, dict)
                and tool.get("type") == "function"
                and tool.get("function", {}).get("name")
            ):
                valid_tools.append(tool)
        return valid_tools

    @staticmethod
    def _serialize_tool_calls(tool_calls: Any) -> list[dict[str, Any]]:
        payload: list[dict[str, Any]] = []
        for tool_call in tool_calls or []:
            fn = getattr(tool_call, "function", None)
            payload.append(
                {
                    "id": getattr(tool_call, "id", ""),
                    "type": "function",
                    "function": {
                        "name": getattr(fn, "name", "") if fn else "",
                        "arguments": getattr(fn, "arguments", "") if fn else "",
                    },
                }
            )
        return payload

    def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        context_message: str = "",
    ) -> dict[str, Any]:
        enriched_messages = list(messages)
        if context_message:
            insert_at = (
                1
                if enriched_messages and enriched_messages[0].get("role") == "system"
                else 0
            )
            enriched_messages.insert(
                insert_at, {"role": "system", "content": context_message}
            )

        valid_tools = self._valid_tools(tools)

        response = self.client.chat.completions.create(
            model=self.model_name,
            messages=enriched_messages,
            tools=valid_tools if valid_tools else None,
            tool_choice="auto" if valid_tools else None,
            temperature=0.8,  # low temp prevents the LLM from entering repeating Chinese/French loops
            presence_penalty=0.1,  # Add small penalty to avoid repeating loops
        )
        msg = response.choices[0].message
        dreamerEvent.set()
        return {
            "role": msg.role,
            "content": msg.content,
            "tool_calls": self._serialize_tool_calls(msg.tool_calls),
        }
