"""
messageHandler.py - Message management for AVA conversations.

Manages the message list (conversation history sent to the LLM),
handles adding messages, resetting conversations, and injecting
dynamic context (memories, conversation history, pending tasks).

System prompt and tool definitions have been extracted to:
    core/system_prompt.py     - get_system_prompt()
    core/tool_definitions.py   - tools
"""

import os
import threading

import requests
from core.AppStates import main_runner
from core.server_api import add_remote_message
from core.system_prompt import get_system_prompt
from core.TaskManager import get_pending_completions_summary
from core.tool_definitions import tools

messages_lock = threading.Lock()

# Import conversation manager (lazy to avoid circular imports)
_conversation_manager = None


def _get_conversation_manager():
    global _conversation_manager
    if _conversation_manager is None:
        from knowledge.ConversationManager import get_manager

        _conversation_manager = get_manager()
    return _conversation_manager


def add_message(content, tool_id="", role="user", trigger_response=None):
    """Add a message to both the current messages list and the conversation history."""
    if trigger_response is None:
        trigger_response = role in {"user", "tool"}

    if role == "tool":
        with messages_lock:
            messages.append(
                {"role": "tool", "content": content, "tool_call_id": tool_id}
            )
        mgr = _get_conversation_manager()
        if mgr.current_conversation:
            mgr.current_conversation.add_message("tool", content, tool_id)
        add_remote_message(role="tool", content=content, tool_id=tool_id)
        if trigger_response and not main_runner.is_set():
            main_runner.set()
    elif role == "user":
        with messages_lock:
            messages.append({"role": role, "content": content})
        mgr = _get_conversation_manager()
        if mgr.current_conversation:
            mgr.current_conversation.add_message("user", content)
        add_remote_message(role="user", content=content)
        if trigger_response and not main_runner.is_set():
            main_runner.set()
    elif role == "assistant":
        with messages_lock:
            messages.append({"role": role, "content": content})
        mgr = _get_conversation_manager()
        if mgr.current_conversation:
            mgr.current_conversation.add_message("assistant", content)
        add_remote_message(role="assistant", content=content)
    else:
        print("[MsgHandler] Invalid role")


def add_assistant_message(content=None, tool_calls=None):
    """
    Preserve assistant text and tool calls in a single history item so one
    turn can say what it is doing and then continue after tool execution.
    """
    payload = {"role": "assistant"}
    if content is not None:
        clean = content.replace("[stop_chat]", "").replace("[STOP_CHAT]", "").strip()
        payload["content"] = clean
    if tool_calls:
        payload["tool_calls"] = tool_calls

    with messages_lock:
        messages.append(payload)

    mgr = _get_conversation_manager()
    if mgr.current_conversation and content:
        mgr.current_conversation.add_message("assistant", content)

    if content:
        add_remote_message(role="assistant", content=content)


def reset_messages():
    """Reset messages to just the system prompt for a new conversation."""
    global messages
    with messages_lock:
        system_prompt = messages[0] if messages else None

        messages.clear()
        if system_prompt:
            sp = system_prompt["content"]
            sp = sp.replace("<replaceMemoriesHere>", get_memory_context())

            conv_context = get_conversation_context()
            conv_context = conv_context.replace("[stop_chat]", "").replace("[STOP_CHAT]", "").strip()
            sp = sp.replace("<replaceConversationContextHere>", conv_context)

            pending_completions = get_pending_completions_summary()
            if pending_completions:
                sp = sp.replace("<replacePendingCompletionsHere>", pending_completions)
            else:
                sp = sp.replace("<replacePendingCompletionsHere>", "")

            promptFinal = {"role": "system", "content": sp}
            messages.append(promptFinal)


def get_memory_context():
    """Get memory context to inject into conversation."""
    SERVER_URL = os.getenv("AVA_SERVER_URL")
    response = requests.get(f"{SERVER_URL}/memories")
    data = response.json()["memories"]
    return data


def get_conversation_context():
    """Get past conversations context."""
    try:
        from knowledge.ConversationManager import get_past_conversations_context

        return get_past_conversations_context()
    except Exception as e:
        print(f"[MsgHandler] Conversation context error: {e}")
        return ""


messages = [
    {
        "role": "system",
        "content": get_system_prompt(),
    }
]
