import json
import functions as functions
from knowledge.memory import handle_memory_manager
from core.TaskManager import dispatch_background_task, get_running_tasks_summary
from knowledge.ConversationManager import handle_conversation_history


def handle_background_task(task_type: str, **kwargs) -> str:
    """
    Handler for background task dispatch.
    Returns an acknowledgment message for the LLM.
    """
    task_id, message = dispatch_background_task(task_type, **kwargs)
    if task_id:
        return message
    return f"Failed to start background task: {message}"


def handle_get_background_status() -> str:
    """Get status of all running background tasks"""
    return get_running_tasks_summary()


TOOL_CONFIGS = {
        # 'timer': functions.timer,
        'music_control': functions.music_control,
        'get_weather_info': functions.get_weather,
        'image_description_tool': functions.image_tool,
        'get_time_date': functions.timedate,
        'shutdown_pc': functions.system_action,
        'webdata': functions.get_google_ai_response,
        'get_url_results': functions.fetch_website_data,
        'link_data': functions.fetch_website_data,
        'save_text': functions.save_text,
        'light_control': functions.control_lights,
        'get_current_location': functions.get_gps_location,
        'create_file': functions.create_file,
        'open_file': functions.open_file,
        'delete_file': functions.delete_file,
        'list_files': functions.list_files,
        'code_executor': functions.run_code_in_sandbox,
        'memory_manager': handle_memory_manager,
        # Background task handlers
        'background_task': handle_background_task,
        'get_background_tasks_status': handle_get_background_status,
        # Conversation history
        'conversation_history': handle_conversation_history,
        'createPDF': functions.create_pdf,
        # 'music_control': functions.mc.control,
        'ping': functions.ring_timer,
    }


def handle_tool_call(tool_call):
    """Execute the requested tool call."""
    tool_id = None  # Initialize to avoid reference before assignment
    try:
        # Support both OpenAI SDK tool_call objects and plain dict payloads
        if isinstance(tool_call, dict):
            tool_id = tool_call.get('id')
            function_payload = tool_call.get('function', {}) or {}
            args_raw = function_payload.get('arguments', '{}')
            func_name = function_payload.get('name')
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

        function = TOOL_CONFIGS.get(func_name)

        if function:
            result = function(**args)
            return result if result is not None else "Success", tool_id
        else:
            return f"Unknown function: {func_name}", tool_id
    except Exception as e:
        if isinstance(tool_call, dict):
            fname = tool_call.get('function', {}).get('name', 'unknown')
        else:
            fname = tool_call.function.name
        print(f"[FuncHandler] Error calling {fname}: {e}")
        return f"Error: {str(e)}", tool_id if tool_id else "unknown"
