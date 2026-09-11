from __future__ import annotations

import os
from typing import Any, List, Optional

from services.key_rotator import chat_completions_create, collect_openai_keys


def _is_valid_image_part(part: dict[str, Any]) -> bool:
    if not isinstance(part, dict):
        return False
    if part.get("type") != "image_url":
        return True
    url = str(part.get("image_url", {}).get("url") or "").strip()
    if url.startswith("http://") or url.startswith("https://"):
        return True
    if url.startswith("data:"):
        header = url.split(";", 1)[0].lower()
        return header.startswith("data:image/")
    return False


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

    def get_tier_info(self, tier: Optional[str]) -> dict[str, Any]:
        """Look up configuration for a tier from App/models_config.json."""
        if not tier:
            return {}
        norm = str(tier).strip().lower().replace("-", "_").replace(" ", "_")
        alias_map = {
            "nano": "low",
            "small": "medium_low",
            "main": "medium",
            "heavy": "high",
            "low": "low",
            "medium_low": "medium_low",
            "mediumlow": "medium_low",
            "medium": "medium",
            "high": "high",
            "very_high": "very_high",
            "veryhigh": "very_high",
            "best": "very_high",
            "flagship": "very_high",
        }
        canonical = alias_map.get(norm, norm)
        try:
            from pathlib import Path
            import json

            candidates = [
                Path(__file__).resolve().parent.parent.parent / "App" / "models_config.json",
                Path(__file__).resolve().parent.parent / "models_config.json",
            ]
            for cfg_path in candidates:
                if cfg_path.exists():
                    with open(cfg_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        tiers = data.get("tiers", {})
                        if canonical in tiers:
                            return tiers[canonical]
        except Exception:
            pass
        return {}

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

        tier_info = self.get_tier_info(tier)
        if tier_info.get("model"):
            return str(tier_info["model"]).strip()

        mapping = {
            "low": os.getenv("AVA_MODEL_LOW") or os.getenv("AVA_MODEL_NANO") or main,
            "medium_low": os.getenv("AVA_MODEL_MEDIUM_LOW") or os.getenv("AVA_MODEL_SMALL") or main,
            "medium": main,
            "high": os.getenv("AVA_MODEL_HIGH") or os.getenv("AVA_MODEL_HEAVY") or main,
            "very_high": os.getenv("AVA_MODEL_VERY_HIGH") or main,
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

        # Ensure multimodal images from tools are properly presented so vision models can ingest the pixels
        prepared_messages: list[dict[str, Any]] = []
        for m in enriched_messages:
            if m.get("role") == "tool" and isinstance(m.get("content"), list):
                tool_texts: list[str] = []
                image_parts: list[dict[str, Any]] = []
                for p in m["content"]:
                    if isinstance(p, dict):
                        if p.get("type") == "text":
                            tool_texts.append(str(p.get("text") or ""))
                        elif p.get("type") == "image_url":
                            if _is_valid_image_part(p):
                                image_parts.append(p)
                            else:
                                tool_texts.append("[non-image attachment omitted]")

                text_content = " ".join(t for t in tool_texts if t).strip() or "Image loaded."
                prepared_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": m.get("tool_call_id", ""),
                        "content": text_content,
                    }
                )
                if image_parts:
                    prepared_messages.append(
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": f"Here is the visual image data for {text_content}:",
                                },
                                *image_parts,
                            ],
                        }
                    )
            elif isinstance(m.get("content"), list):
                clean_parts = []
                for p in m["content"]:
                    if isinstance(p, dict) and p.get("type") == "image_url":
                        if _is_valid_image_part(p):
                            clean_parts.append(p)
                        else:
                            clean_parts.append({
                                "type": "text",
                                "text": "[non-image attachment omitted from vision input]"
                            })
                    else:
                        clean_parts.append(p)
                sanitized_msg = dict(m)
                sanitized_msg["content"] = clean_parts
                prepared_messages.append(sanitized_msg)
            else:
                prepared_messages.append(m)

        # Ensure at least one user message exists so backends like Codex don't error with
        # "Invalid type for 'input': expected a string, but got an object instead."
        if not any(m.get("role") == "user" for m in prepared_messages):
            prepared_messages.append({"role": "user", "content": "Please proceed."})

        for m in prepared_messages:
            if m.get("role") == "user" and not m.get("content"):
                m["content"] = " "

        # Ensure every tool message has a matching preceding assistant tool_call id
        # to prevent OpenAI 400 "No tool call found for function call output with call_id..."
        known_tool_call_ids = set()
        for m in prepared_messages:
            if m.get("role") == "assistant" and m.get("tool_calls"):
                for tc in m["tool_calls"]:
                    if isinstance(tc, dict) and tc.get("id"):
                        known_tool_call_ids.add(tc["id"])
            elif m.get("role") == "tool":
                tid = m.get("tool_call_id")
                if not tid or tid not in known_tool_call_ids:
                    m["role"] = "user"
                    m["content"] = f"[Tool output for call {tid or 'unknown'}]: {m.get('content')}"
                    m.pop("tool_call_id", None)

        valid_tools = self._valid_tools(tools)
        model_id = self.resolve_model(model=model, model_tier=model_tier)
        tier_info = self.get_tier_info(model_tier)
        call_kwargs: dict[str, Any] = {
            "presence_penalty": 0.1,
        }
        if "temperature" in tier_info and tier_info["temperature"] is not None:
            call_kwargs["temperature"] = float(tier_info["temperature"])
        if "max_tokens" in tier_info and tier_info["max_tokens"] is not None:
            call_kwargs["max_tokens"] = int(tier_info["max_tokens"])
        effective_base_url = tier_info.get("base_url") or self.base_url

        try:
            response = chat_completions_create(
                model=model_id,
                messages=prepared_messages,
                base_url=effective_base_url,
                label="MainLLM",
                tools=valid_tools if valid_tools else None,
                tool_choice="auto" if valid_tools else None,
                **call_kwargs,
            )
        except Exception as exc:
            exc_str = str(exc)
            if "image_url" in exc_str or "MIME" in exc_str or "400" in exc_str:
                print(f"[LLMService] Multimodal format error ({exc}), retrying with text-only messages...")
                fallback_messages = []
                for msg in prepared_messages:
                    content = msg.get("content")
                    if isinstance(content, list):
                        texts = [
                            str(p.get("text", ""))
                            for p in content
                            if isinstance(p, dict) and p.get("type") == "text"
                        ]
                        fallback_messages.append({
                            "role": msg.get("role", "user"),
                            "content": " ".join(t for t in texts if t).strip() or "[attachment]"
                        })
                    else:
                        fallback_messages.append(msg)
                response = chat_completions_create(
                    model=model_id,
                    messages=fallback_messages,
                    base_url=self.base_url,
                    label="MainLLM-Fallback",
                    tools=valid_tools if valid_tools else None,
                    tool_choice="auto" if valid_tools else None,
                    presence_penalty=0.1,
                )
            else:
                raise
        msg = response.choices[0].message
        usage_payload: dict[str, Any] = {}
        usage = getattr(response, "usage", None)
        if usage is not None:
            try:
                usage_payload = {
                    "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
                    "completion_tokens": int(
                        getattr(usage, "completion_tokens", 0) or 0
                    ),
                    "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
                }
            except (TypeError, ValueError):
                usage_payload = {}
        return {
            "role": msg.role,
            "content": msg.content,
            "tool_calls": self._serialize_tool_calls(msg.tool_calls),
            "model": model_id,
            "model_tier": model_tier or "main",
            "usage": usage_payload,
        }
