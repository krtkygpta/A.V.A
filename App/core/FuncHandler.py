import json

import functions as functions
from core.TaskManager import (
    CompletionQueue,
    ConversationStateManager,
    dispatch_background_task,
    get_running_tasks_summary,
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
    """
    try:
        # Try to get from ConversationManager first
        conv = get_current_conversation()
        if conv and conv.id:
            return conv.id, conv.name
        
        # Fallback to state manager
        state_mgr = ConversationStateManager()
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
        result += "\n\nThere are completed background tasks waiting to be announced. They will be shown when you start a new conversation."
    
    return result


TOOL_CONFIGS = {
    # Media
    "music_agent": functions.run_music_controller,
    "image_description_tool": functions.image_tool,
    # System
    "get_time_date": functions.timedate,
    "shutdown_pc": functions.system_action,
    "ping": functions.ring_timer,
    "send_notification": functions.send_notification,
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

        if function:
            result = function(**args)
            if result is None:
                result = "Success"
            elif not isinstance(result, str):
                # OpenAI requires tool content to be a string — coerce defensively
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
