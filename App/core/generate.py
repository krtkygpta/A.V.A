from core.messageHandler import (
    add_assistant_message,
    add_message,
    messages,
    messages_for_llm,
    tools,
)
from core.server_api import generate_remote_response, generate_remote_response_stream
from core.tool_registry import init_plugins, registry


def get_all_tools():
    """Merge static tools with plugin tools"""
    init_plugins()  # Ensure plugins are loaded
    return list(tools) + registry.get_schema()


def _open_main_action_text() -> str | None:
    """If a completed job still needs main-agent tools, return the action text."""
    try:
        from core.TaskManager import list_open_main_actions

        items = list_open_main_actions()
        if not items:
            return None
        return items[0]["action"]
    except Exception:
        return None


def _fallback_from_done_inject() -> str | None:
    """
    When the model returns empty on a [BACKGROUND TASK DONE] wake, speak a
    short summary from the inject instead of 'I'm not sure how to respond'.

    Never use a pure-ack fallback when a main_action is still open — that
    caused "Background task update received" while skipping WhatsApp/weather.
    """
    if _open_main_action_text():
        return None
    try:
        for msg in reversed(list(messages)):
            if msg.get("role") != "system":
                continue
            text = str(msg.get("content") or "")
            if "[BACKGROUND TASK DONE]" not in text and "[BACKGROUND TASK FAILED]" not in text:
                if "[SERVICE EVENT]" not in text:
                    continue
            # Pending main action still in inject — do not soft-ack
            if "REQUIRED ACTION" in text or "PENDING MAIN ACTION" in text:
                return None
            summary = ""
            finished = ""
            for line in text.splitlines():
                low = line.strip()
                if low.lower().startswith("summary:"):
                    summary = low.split(":", 1)[1].strip()
                elif low.lower().startswith("finished:"):
                    finished = low.split(":", 1)[1].strip()
                elif low.lower().startswith("title="):
                    finished = low.split("=", 1)[1].strip()
            if summary:
                spoken = summary.split("|")[0].strip()
                # Strip "Next: main agent should do..." noise from spoken line
                for sep in ("| Next:", "| next:", "Next: main agent"):
                    if sep in spoken:
                        spoken = spoken.split(sep)[0].strip()
                if len(spoken) > 220:
                    spoken = spoken[:217].rsplit(" ", 1)[0] + "..."
                # Never fall back to a greeting-like line
                low = spoken.lower()
                if low.startswith(("hey", "hello", "hi ", "what can i")):
                    spoken = "OpenCode finished. " + spoken
                return spoken
            # type=opencode lines
            session_line = ""
            dir_line = ""
            for line in text.splitlines():
                low = line.strip().lower()
                if low.startswith("session_id="):
                    session_line = line.strip().split("=", 1)[-1]
                if low.startswith("dir="):
                    dir_line = line.strip().split("=", 1)[-1]
            if finished:
                return f"Done: {finished[:160]}"
            if dir_line:
                return f"OpenCode finished in {dir_line[:120]}."
            if session_line:
                return "OpenCode finished."
            return "Background task finished."
    except Exception:
        pass
    return None


def generate_response(on_chunk=None):
    try:
        open_action = _open_main_action_text()

        if on_chunk is not None:
            server_message = None
            for event_type, data in generate_remote_response_stream(
                messages_for_llm(),
                get_all_tools(),
                include_server_context=False,
            ):
                if event_type == "chunk":
                    try:
                        on_chunk(data)
                    except Exception:
                        pass
                elif event_type == "final":
                    server_message = data

            if not server_message:
                server_message = {"role": "assistant", "content": None, "tool_calls": []}
        else:
            server_message = generate_remote_response(
                messages_for_llm(),
                get_all_tools(),
                include_server_context=False,
            )

        message = {
            "role": server_message.get("role", "assistant"),
            "content": server_message.get("content"),
            "tool_calls": server_message.get("tool_calls") or [],
        }

        content = message.get("content")
        tool_calls = message.get("tool_calls") or []

        if isinstance(content, str):
            # Clean markdown bold/italic asterisks from spoken responses (e.g. **word** -> word)
            import re
            cleaned_content = re.sub(r"\*\*([^*]+)\*\*", r"\1", content)
            cleaned_content = re.sub(r"\*([^*]+)\*", r"\1", cleaned_content)
            cleaned_content = re.sub(r"__([^_]+)__", r"\1", cleaned_content)
            cleaned_content = re.sub(r"^[#\s]+", "", cleaned_content, flags=re.MULTILINE)
            content = cleaned_content.strip()
            if not content:
                content = None
            message["content"] = content

        # Model returned text only while a main_action still needs tools —
        # treat as incomplete so the response loop can re-nudge (flag only).
        if open_action and not tool_calls:
            message["_main_action_needs_tools"] = True
            message["_main_action_text"] = open_action

        if content is not None or tool_calls:
            saved = add_assistant_message(content=content, tool_calls=tool_calls)
            if saved is False and not tool_calls:
                message["_duplicate_assistant"] = True
        else:
            fallback = _fallback_from_done_inject()
            if fallback:
                print(f"[Gen] Empty model reply on DONE — using inject summary")
                add_message(content=fallback, role="assistant")
                message["content"] = fallback
            elif open_action:
                # Do not invent "I'm not sure" — leave empty content; loop will re-nudge
                print(
                    f"[Gen] Empty reply with open main_action — forcing tool nudge path"
                )
                message["_main_action_needs_tools"] = True
                message["_main_action_text"] = open_action
                message["content"] = None
            else:
                add_message(
                    content="I'm not sure how to respond to that.", role="assistant"
                )
                message["content"] = "I'm not sure how to respond to that."

        return message

    except Exception as e:
        error_msg = str(e)
        print(f"[Gen] API error: {error_msg}")

        error_response = (
            "I encountered an error while processing your request. Please try again."
        )
        add_message(content=error_response, role="assistant")

        return {
            "role": "assistant",
            "content": error_response,
            "error": True,
            "error_details": error_msg,
        }
