import json

import functions as functions
from core.TaskManager import (
    CompletionQueue,
    ConversationStateManager,
    dispatch_background_task,
    get_running_tasks_summary,
    manage_background_tasks,
    update_conversation_state,
)
from core.tool_registry import init_plugins, registry
from functions.productivity.calendar import calendar
from functions.system.bash_executor import run_bash
from functions.web.internet import web
from knowledge.ConversationManager import handle_conversation_history, get_manager, get_current_conversation
from knowledge.memory import handle_memory_manager

# Initialize plugins on module load
init_plugins()


def _get_current_conversation_info() -> tuple:
    """
    Get current conversation ID and name for task association.
    Returns (conversation_id, conversation_name)

    Also re-syncs ConversationStateManager when ConversationManager has a live
    conversation (avoids empty conversation_id on background jobs).
    """
    try:
        state_mgr = ConversationStateManager()
        # Prefer ConversationManager (source of truth for chat history).
        conv = get_current_conversation()
        if conv and conv.id:
            if state_mgr.current_conversation_id != conv.id:
                try:
                    update_conversation_state(conv.id, conv.name or "", is_active=True)
                except Exception:
                    pass
            return conv.id, conv.name or ""

        # Fallback to state manager
        conv_id = state_mgr.current_conversation_id or ""
        conv_name = state_mgr.current_conversation_name or ""
        return conv_id, conv_name
    except Exception:
        return "", ""


def handle_background_task(task_type: str, **kwargs) -> str:
    """
    Handler for background task dispatch.
    Returns an acknowledgment message for the LLM.

    ENHANCED: Now passes conversation context for proper task tracking
    and signals that the conversation should stay open for completion.
    """
    # Get current conversation context
    conversation_id, conversation_name = _get_current_conversation_info()

    # Signal that we're expecting follow-up (background task completion)
    from core.AppStates import expecting_followup, work_in_progress, background_task_active
    expecting_followup.set()
    work_in_progress.set()
    background_task_active.set()

    # Pass conversation context to dispatch
    task_id, message = dispatch_background_task(
        task_type,
        conversation_id=conversation_id,
        conversation_name=conversation_name,
        **kwargs
    )

    if task_id:
        try:
            from core.TaskManager import sync_background_task_flag

            sync_background_task_flag()
        except Exception:
            pass
        return message

    # Clear flags if task failed to start
    expecting_followup.clear()
    work_in_progress.clear()
    background_task_active.clear()
    return f"Failed to start background task: {message}"


def handle_get_background_status() -> str:
    """Get status of all running background tasks and pending completions"""
    result = get_running_tasks_summary()

    # Also report any completed-but-undelivered background tasks
    completion_queue = CompletionQueue()
    if completion_queue.has_pending():
        result += (
            "\n\nThere are completed background tasks waiting to be announced. "
            "They should inject as [BACKGROUND TASK DONE] on the next turn or "
            "conversation start — speak the newest one; use manage_background_tasks "
            "result if the user wants full detail."
        )

    return result


TOOL_CONFIGS = {
    # Media
    # "music_agent": functions.run_music_controller,
    # "image_description_tool": functions.image_tool,
    # System
    "get_time_date": functions.timedate,
    "shutdown_pc": functions.system_action,
    "ping": functions.ping,  # non-blocking audible alert (ring in background)
    "send_notification": functions.send_notification,
    "make_phone_call": functions.make_phone_call,
    "screenshot": functions.screenshot,
    # Web (unified)
    "web": web,
    # File operations
    "save_text": functions.save_text,
    "create_file": functions.create_file,
    "open_file": functions.open_file,
    "delete_file": functions.delete_file,
    "list_files": functions.list_files,
    "create_pdf": functions.create_pdf,
    # Code execution
    "code_executor": functions.run_code_in_sandbox,
    # Calendar
    "calendar": calendar,
    # Bash
    "bash": run_bash,
    # Memory & Tasks
    "memory_manager": handle_memory_manager,
    "background_task": handle_background_task,
    "get_background_tasks_status": handle_get_background_status,
    "manage_background_tasks": manage_background_tasks,
    "conversation_history": handle_conversation_history,
}


def handle_tool_call(tool_call):
    """Execute the requested tool call."""
    tool_id = None  # Initialize to avoid reference before assignment
    try:
        # Support both OpenAI SDK tool_call objects and plain dict payloads
        if isinstance(tool_call, dict):
            tool_id = tool_call.get("id")
            function_payload = tool_call.get("function", {}) or {}
            args_raw = function_payload.get("arguments", "{}")
            func_name = function_payload.get("name")
        else:
            tool_id = tool_call.id
            args_raw = tool_call.function.arguments
            func_name = tool_call.function.name

        if isinstance(args_raw, str):
            args = json.loads(args_raw) if args_raw.strip() else {}
        elif isinstance(args_raw, dict):
            args = args_raw
        else:
            args = {}

        function = registry.get(func_name) or TOOL_CONFIGS.get(func_name)
        args = {k: v for k, v in args.items() if k != ""}

        if function:
            result = function(**args)
            if result is None:
                result = "Success"
            elif isinstance(result, list):
                # Multimodal tool content (e.g. screenshot text + image_url parts).
                # Pass through so the model receives the image like test.py.
                pass
            elif not isinstance(result, str):
                # Most tools return strings; coerce other types to JSON text.
                import json as _json

                try:
                    result = _json.dumps(result)
                except Exception:
                    result = str(result)
            return result, tool_id
        else:
            return f"Unknown function: {func_name}", tool_id
    except Exception as e:
        if isinstance(tool_call, dict):
            fname = tool_call.get("function", {}).get("name", "unknown")
        else:
            fname = tool_call.function.name
        print(f"[FuncHandler] Error calling {fname}: {e}")
        return f"Error: {str(e)}", tool_id if tool_id else "unknown"
