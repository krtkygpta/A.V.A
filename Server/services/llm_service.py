from __future__ import annotations

import os
from typing import Any, List, Optional

from services.key_rotator import chat_completions_create, collect_openai_keys


class LLMService:
    """Chat completion via shared OPENAI_API_KEYS pool (round-robin + 429 retry)."""

    def __init__(
        self,
        api_key: str | None,
        base_url: str | None,
        model_name: str,
        api_keys: Optional[List[str] | str] = None,
    ):
        self.base_url = base_url
        self.model_name = model_name
        # Warm / validate pool (also used by research + dreamer)
        keys = collect_openai_keys(primary=api_key, multi=api_keys, env=True)
        self._pool_size = len(keys)
        if keys:
            print(
                f"[LLMService] shared key pool n={self._pool_size} "
                f"(main / research / dreamer)"
            )
        else:
            print("[LLMService] WARNING: no OPENAI_API_KEY(S) configured")

    def resolve_model(
        self,
        model: Optional[str] = None,
        model_tier: Optional[str] = None,
    ) -> str:
        """Pick a concrete model id from explicit model or tier."""
        if model and str(model).strip():
            return str(model).strip()
        tier = (model_tier or "main").lower()
        main = self.model_name or os.getenv("MODEL_NAME") or ""
        if tier == "none":
            return main
        mapping = {
            "nano": os.getenv("AVA_MODEL_NANO") or os.getenv("AVA_MODEL_SMALL") or main,
            "small": os.getenv("AVA_MODEL_SMALL") or main,
            "main": main,
            "heavy": os.getenv("AVA_MODEL_HEAVY") or main,
        }
        return (mapping.get(tier) or main).strip() or main

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
        model: Optional[str] = None,
        model_tier: Optional[str] = None,
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
        model_id = self.resolve_model(model=model, model_tier=model_tier)

        response = chat_completions_create(
            model=model_id,
            messages=enriched_messages,
            base_url=self.base_url,
            label="MainLLM",
            tools=valid_tools if valid_tools else None,
            tool_choice="auto" if valid_tools else None,
            temperature=0.8,
            presence_penalty=0.1,
        )
        msg = response.choices[0].message
        return {
            "role": msg.role,
            "content": msg.content,
            "tool_calls": self._serialize_tool_calls(msg.tool_calls),
            "model": model_id,
            "model_tier": model_tier or "main",
        }
