from __future__ import annotations

import os
from typing import Any, List, Optional

from services.key_rotator import (
    chat_completions_create,
    chat_completions_stream,
    collect_openai_keys,
)


def get_server_system_prompt() -> str:
    """
    System prompt for server-side clients (web, Android).
    Adapted from TUI's AVA prompt for text-based interfaces.
    """
    return """<!--
    name: "AVA — Ahead of Virtually Anyone"
    description: "Core system prompt for AVA, personal agent harness (server/web/Android)"
    -->

    You are AVA — Ahead of Virtually Anyone. You are a personal agent — an operator, not a Q&A bot.

    IDENTITY: Capable, dry wit, loyal. Be direct and helpful without unnecessary filler.

    CORE DIRECTIVE — SOLVE, DON'T STALL:
    - Tool/path fails → don't report failure as final answer. Try a different tool, argument, or combination first.
    - Chain tools in ways not pre-wired: e.g. image analysis fails → try google_ai search → memory_manager to save the answer.
    - Before asking the user anything, check if memory, a quick tool call, or a reasonable default already answers it. Ask only for decisions that are genuinely theirs (money, sending on their behalf, irreversible actions).
    - Multi-step job: execute, don't narrate.
    - Never invent a result, send, path, or finding. No tool output = didn't happen. Verify via manage_background_tasks/get_background_tasks_status when unsure.

    RESPONSE FORMAT (text-based clients — web & Android):
    - Use clear, well-formatted Markdown for readability.
    - Use headers, bullet points, code blocks, and tables where appropriate.
    - Keep responses concise but complete.
    - For code, reports, or structured output: use proper formatting with syntax highlighting.

    TOOL ROUTING:
    Web/Search→google_ai(query) | time/date→device_time(target_device="caller") | unknown fact→google_ai
    battery→device_battery(target_device=<device>) | location→device_location(target_device=<device>)
    ring device→device_ring(target_device=<device>) | music→device_music(target_device=<device>, action=<play/pause/stop/next/volume>)
    device bash/screenshot/save_file→device_tool(target_device=<device>, tool_name=<tool>, arguments={...})
    image analysis→image_analysis(image_path=<server_path>) or view_image(image_path=<server_path>)
    code execution→code_execute(code=<python>) | research→background_task(task_type="research", question=<q>, level=1-5)
    coding/build→background_task(task_type="coding", objective=<instruction>, target_dir=<path>)
    background worker→background_task(task_type=<type>, objective=<instruction>)
    job control→manage_background_tasks(action=<list/status/result/cancel/dismiss>, task_id=<id>)
    check jobs→get_background_tasks_status()
    search tools→tool_search(query=<text>) | save fact→memory_manager(action="save", key=<k>, value=<v>)
    recall fact→memory_manager(action="retrieve") BEFORE answering questions about user
    view history→conversation_history(action="get"|"list"|"search")
    file uploads→images are automatically analyzed when attached to messages

    BACKGROUND JOBS:
    Jobs survive turns. "Run/continue what you built" → pull context/result via manage_background_tasks, then act with real tools.
    Speak about a job only if: just completed this turn, or the user asked.

    MEMORY: Save durable facts immediately via memory_manager(save), no waiting. Retrieve before answering anything about the user. Categories: preferences, facts, habits, relationships, work, health, projects.

    TURN LOGIC (first match wins):
    A. Pending required action (finished job/timer) → run real tool, one outcome line.
    B. Job needs input / permission → evaluate safety, grant if safe via manage_background_tasks(action="answer").
    C. Background job just completed → speak outcome only, no greeting.
    D. Normal message → tools per CORE DIRECTIVE, then reply. Clear sign-off ("thanks"/"bye") → end conversation.

    CONTEXT (injected each turn):
    MEMORIES: <injected from memory store>
    DEVICES: <injected device presence>
    RECENT CHATS: <injected conversation history>
    CLOCK: <injected current time/date>
"""


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
        self.api_key = api_key
        self.api_keys = [api_key] if api_key and not api_keys else api_keys
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

    def _prepare_call(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        context_message: str = "",
        model: Optional[str] = None,
        model_tier: Optional[str] = None,
    ):
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

        # 1. Normalize parts and extract any multimodal images safely
        normalized_messages: list[dict[str, Any]] = []
        pending_image_parts: list[dict[str, Any]] = []

        for m in enriched_messages:
            msg = dict(m)
            role = msg.get("role", "user")
            msg["role"] = role

            if role == "tool":
                content = msg.get("content")
                if isinstance(content, list):
                    tool_texts: list[str] = []
                    for p in content:
                        if isinstance(p, dict):
                            if p.get("type") == "text":
                                tool_texts.append(str(p.get("text") or ""))
                            elif p.get("type") == "image_url":
                                if _is_valid_image_part(p):
                                    pending_image_parts.append(p)
                                else:
                                    tool_texts.append("[attachment]")
                    msg["content"] = " ".join(t for t in tool_texts if t).strip() or "Tool executed."
                elif content is None:
                    msg["content"] = "Tool executed."
                normalized_messages.append(msg)
            elif role == "assistant":
                tcs = msg.get("tool_calls")
                if not tcs:
                    msg.pop("tool_calls", None)
                else:
                    valid_tcs = [tc for tc in tcs if isinstance(tc, dict) and tc.get("id")]
                    if valid_tcs:
                        msg["tool_calls"] = valid_tcs
                    else:
                        msg.pop("tool_calls", None)
                normalized_messages.append(msg)
                # If there were pending images from a previous tool block, attach them now as user input
                if pending_image_parts:
                    normalized_messages.append({
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Visual tool observation:"},
                            *pending_image_parts,
                        ],
                    })
                    pending_image_parts = []
            elif isinstance(msg.get("content"), list):
                clean_parts = []
                for p in msg["content"]:
                    if isinstance(p, dict) and p.get("type") == "image_url":
                        if _is_valid_image_part(p):
                            clean_parts.append(p)
                        else:
                            clean_parts.append({"type": "text", "text": "[attachment omitted]"})
                    else:
                        clean_parts.append(p)
                msg["content"] = clean_parts
                normalized_messages.append(msg)
            else:
                normalized_messages.append(msg)

        if pending_image_parts:
            normalized_messages.append({
                "role": "user",
                "content": [
                    {"type": "text", "text": "Visual tool observation:"},
                    *pending_image_parts,
                ],
            })
            pending_image_parts = []

        # 2. Strict Tool Sequence Validation for OpenAI compatibility
        prepared_messages: list[dict[str, Any]] = []
        i = 0
        while i < len(normalized_messages):
            curr = normalized_messages[i]
            if curr.get("role") == "assistant" and curr.get("tool_calls"):
                expected_ids = {tc["id"] for tc in curr["tool_calls"] if isinstance(tc, dict) and tc.get("id")}
                j = i + 1
                found_ids = set()
                tool_msgs = []
                while j < len(normalized_messages) and normalized_messages[j].get("role") == "tool":
                    tid = normalized_messages[j].get("tool_call_id")
                    if tid in expected_ids:
                        found_ids.add(tid)
                        tool_msgs.append(normalized_messages[j])
                    else:
                        t_copy = dict(normalized_messages[j])
                        t_copy["role"] = "user"
                        t_copy["content"] = f"[Tool output]: {t_copy.get('content')}"
                        t_copy.pop("tool_call_id", None)
                        tool_msgs.append(t_copy)
                    j += 1

                missing_ids = expected_ids - found_ids
                if missing_ids:
                    if not found_ids and curr.get("content"):
                        # Assistant already has text explanation; prune orphaned tool_calls
                        curr_copy = dict(curr)
                        curr_copy.pop("tool_calls", None)
                        prepared_messages.append(curr_copy)
                    else:
                        prepared_messages.append(curr)
                        prepared_messages.extend(tool_msgs)
                        for mid in missing_ids:
                            prepared_messages.append({
                                "role": "tool",
                                "tool_call_id": mid,
                                "content": '{"status": "completed"}',
                            })
                else:
                    prepared_messages.append(curr)
                    prepared_messages.extend(tool_msgs)
                i = j
            elif curr.get("role") == "tool":
                curr_copy = dict(curr)
                curr_copy["role"] = "user"
                curr_copy["content"] = f"[Tool output]: {curr_copy.get('content')}"
                curr_copy.pop("tool_call_id", None)
                prepared_messages.append(curr_copy)
                i += 1
            else:
                prepared_messages.append(curr)
                i += 1

        # 3. Ensure user and assistant messages satisfy OpenAI non-empty rules
        if not any(m.get("role") == "user" for m in prepared_messages):
            prepared_messages.append({"role": "user", "content": "Please proceed."})

        for m in prepared_messages:
            if m.get("role") == "user" and not m.get("content"):
                m["content"] = " "
            elif m.get("role") == "assistant" and not m.get("content") and not m.get("tool_calls"):
                m["content"] = "[Action completed]"

        valid_tools = self._valid_tools(tools)
        model_id = self.resolve_model(model=model, model_tier=model_tier)
        tier_info = self.get_tier_info(model_tier)
        is_reasoning_model = any(tag in model_id.lower() for tag in ("luna", "o1", "o3", "reasoning", "gpt-5"))
        call_kwargs: dict[str, Any] = {}
        if not is_reasoning_model:
            call_kwargs["presence_penalty"] = 0.1
            if "temperature" in tier_info and tier_info["temperature"] is not None:
                call_kwargs["temperature"] = float(tier_info["temperature"])
        if "max_tokens" in tier_info and tier_info["max_tokens"] is not None:
            call_kwargs["max_tokens"] = int(tier_info["max_tokens"])
        if valid_tools:
            call_kwargs["tools"] = valid_tools
            call_kwargs["tool_choice"] = "auto"
        effective_base_url = tier_info.get("base_url") or self.base_url
        keys_to_use = self.api_keys or ([self.api_key] if self.api_key else None)
        return prepared_messages, valid_tools, model_id, effective_base_url, keys_to_use, call_kwargs

    def generate_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        context_message: str = "",
        model: Optional[str] = None,
        model_tier: Optional[str] = None,
    ):
        """
        Stream assistant response tokens and return final message.
        Yields:
          {"type": "content", "delta": str}
          {"type": "finish", "message": dict}
        """
        prepared_messages, valid_tools, model_id, effective_base_url, keys_to_use, call_kwargs = self._prepare_call(
            messages=messages,
            tools=tools,
            context_message=context_message,
            model=model,
            model_tier=model_tier,
        )

        try:
            stream = chat_completions_stream(
                model=model_id,
                messages=prepared_messages,
                base_url=effective_base_url,
                api_keys=keys_to_use,
                label="MainLLM-Stream",
                **call_kwargs,
            )
            for event in stream:
                if event.get("type") == "content":
                    yield event
                elif event.get("type") == "finish":
                    final_msg = {
                        "role": event.get("role") or "assistant",
                        "content": event.get("content"),
                        "tool_calls": event.get("tool_calls") or [],
                        "model": model_id,
                        "model_tier": model_tier or "main",
                    }
                    yield {"type": "finish", "message": final_msg}
        except Exception as exc:
            exc_str = str(exc)
            print(f"[LLMService] Stream generation error ({exc}), initiating fallback...")
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
            fb_kwargs = dict(call_kwargs)
            if "400" in exc_str or "unsupported" in exc_str.lower() or "param" in exc_str.lower():
                fb_kwargs.pop("temperature", None)
                fb_kwargs.pop("top_p", None)
                fb_kwargs.pop("presence_penalty", None)
                fb_kwargs.pop("frequency_penalty", None)
            try:
                stream = chat_completions_stream(
                    model=model_id,
                    messages=fallback_messages,
                    base_url=effective_base_url,
                    api_keys=keys_to_use,
                    label="MainLLM-StreamFallback",
                    **fb_kwargs,
                )
                for event in stream:
                    if event.get("type") == "content":
                        yield event
                    elif event.get("type") == "finish":
                        final_msg = {
                            "role": event.get("role") or "assistant",
                            "content": event.get("content"),
                            "tool_calls": event.get("tool_calls") or [],
                            "model": model_id,
                            "model_tier": model_tier or "main",
                        }
                        yield {"type": "finish", "message": final_msg}
            except Exception as exc2:
                # Last resort: text-only without tools and without sampling params to guarantee user gets a response
                print(f"[LLMService] Stream fallback error ({exc2}), retrying text-only without tools...")
                fb_kwargs.pop("tools", None)
                fb_kwargs.pop("tool_choice", None)
                fb_kwargs.pop("temperature", None)
                fb_kwargs.pop("top_p", None)
                fb_kwargs.pop("presence_penalty", None)
                fb_kwargs.pop("frequency_penalty", None)
                stream = chat_completions_stream(
                    model=model_id,
                    messages=fallback_messages,
                    base_url=effective_base_url,
                    api_keys=keys_to_use,
                    label="MainLLM-StreamSafeFallback",
                    **fb_kwargs,
                )
                for event in stream:
                    if event.get("type") == "content":
                        yield event
                    elif event.get("type") == "finish":
                        final_msg = {
                            "role": event.get("role") or "assistant",
                            "content": event.get("content"),
                            "tool_calls": [],
                            "model": model_id,
                            "model_tier": model_tier or "main",
                        }
                        yield {"type": "finish", "message": final_msg}

    def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        context_message: str = "",
        model: Optional[str] = None,
        model_tier: Optional[str] = None,
    ) -> dict[str, Any]:
        prepared_messages, valid_tools, model_id, effective_base_url, keys_to_use, call_kwargs = self._prepare_call(
            messages=messages,
            tools=tools,
            context_message=context_message,
            model=model,
            model_tier=model_tier,
        )

        try:
            response = chat_completions_create(
                model=model_id,
                messages=prepared_messages,
                base_url=effective_base_url,
                api_keys=keys_to_use,
                label="MainLLM",
                **call_kwargs,
            )
        except Exception as exc:
            exc_str = str(exc)
            print(f"[LLMService] LLM generation error ({exc}), initiating fallback...")
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
            fb_kwargs = dict(call_kwargs)
            if "400" in exc_str or "unsupported" in exc_str.lower() or "param" in exc_str.lower():
                fb_kwargs.pop("temperature", None)
                fb_kwargs.pop("top_p", None)
                fb_kwargs.pop("presence_penalty", None)
                fb_kwargs.pop("frequency_penalty", None)
            try:
                response = chat_completions_create(
                    model=model_id,
                    messages=fallback_messages,
                    base_url=effective_base_url,
                    api_keys=keys_to_use,
                    label="MainLLM-Fallback",
                    **fb_kwargs,
                )
            except Exception as exc2:
                # Last resort: text-only without tools
                print(f"[LLMService] Fallback error ({exc2}), retrying text-only without tools...")
                fb_kwargs.pop("tools", None)
                fb_kwargs.pop("tool_choice", None)
                fb_kwargs.pop("temperature", None)
                fb_kwargs.pop("top_p", None)
                fb_kwargs.pop("presence_penalty", None)
                fb_kwargs.pop("frequency_penalty", None)
                response = chat_completions_create(
                    model=model_id,
                    messages=fallback_messages,
                    base_url=effective_base_url,
                    api_keys=keys_to_use,
                    label="MainLLM-SafeFallback",
                    **fb_kwargs,
                )
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
