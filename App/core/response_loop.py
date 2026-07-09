"""
response_loop.py - Shared LLM response generation loop for CLI and TUI.

Extracts the ~60 lines of duplicated main loop logic from App/__main__.py
and App/ui/tui.py into a single canonical implementation.

ENHANCED: Now includes conversation-aware background task notification handling
for all three cases:
- Case 1: Original conversation still active
- Case 2: No conversation active (wake-word mode)
- Case 3: Different conversation active

Usage (CLI):
    handler = CLIResponseLoopHandler()
    threading.Thread(target=run_response_loop, args=(handler,), daemon=True).start()

Usage (TUI):
    handler = TUIResponseLoopHandler(app)
    threading.Thread(target=run_response_loop, args=(handler,), daemon=True).start()
"""

import json
import os
import re
import sys
import threading

# Add the App directory to path for relative imports
_APP_DIR = os.path.dirname(os.path.dirname(__file__))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from core.AppStates import (
    main_runner,
    stop_chat_flag,
    expecting_followup,
    work_in_progress,
    background_task_active,
)
from core.FuncHandler import handle_tool_call
from core.generate import generate_response
from core.messageHandler import add_message
from core.TaskManager import (
    CompletionQueue,
    ConversationStateManager,
    NotificationDecisionEngine,
    TaskRegistry,
    check_and_format_completions,
    update_conversation_state,
    set_speaking_state,
    set_listening_state,
    set_mode,
    user_activity_occurred,
)
from core.TaskManager import BackgroundTask, TaskStatus, DeliveryMethod, TaskPriority


# Work-in-progress detection keywords
WORK_IN_PROGRESS_KEYWORDS = {
    "fetching", "looking up", "checking", "searching for", "searching",
    "processing", "working on", "one moment", "please wait", "hold on",
    "loading", "retrieving", "getting", "preparing", "calculating",
    "connecting to", "connecting", "wait", "let me check", "let me see",
    "give me a moment", "i'll check", "i'm checking", "i need to",
    "hold on a second", "just a moment"
}

# Pattern for explicit keep-open markers
KEEP_OPEN_PATTERN = re.compile(r'\[(?:keep_open|continue|stay_open|wait)\]', re.IGNORECASE)


def _is_work_in_progress(text: str) -> bool:
    """
    Check if assistant response indicates work is still in progress.
    
    Returns True if the response suggests the model will have more to say later,
    which means we should keep the conversation open longer.
    """
    if not text:
        return False
    
    text_lower = text.lower()
    
    # Check for explicit keep-open markers
    if KEEP_OPEN_PATTERN.search(text):
        return True
    
    # Check for work-in-progress keywords
    for keyword in WORK_IN_PROGRESS_KEYWORDS:
        if keyword in text_lower:
            return True
    
    return False


# Shared event that tracks whether the loop is actively processing a response.
# Voice / text mode functions use this to wait for responses to complete.
main_running = threading.Event()


class ResponseLoopHandler:
    """Base handler for the shared response loop.

    Subclasses override hook methods to customize UI behavior.
    The default no-op hooks are suitable for CLI mode.

    Hook methods (override as needed):
        speak(text)               - Display/speak assistant text
        on_tool_call_start(...)   - Tool call began executing
        on_tool_call_result(...)  - Tool call finished
        on_processing_end()       - Response cycle finished
        on_background_task_notify(summary) - Background task completed while idle
        on_immediate_notification(text) - High priority task needs immediate attention
        on_idle_notification(task) - Task completed while in idle/wake-word mode
    """

    def speak(self, text: str):
        """Display or speak assistant text to the user."""
        raise NotImplementedError

    def on_tool_call_start(self, func_name: str, args: dict):
        """Called when a tool call begins executing."""
        pass

    def on_tool_call_result(self, result: str):
        """Called when a tool call completes with its result."""
        pass

    def on_processing_end(self):
        """Called after each LLM response cycle completes."""
        pass

    def on_background_task_notify(self, summary: str):
        """Called when a background task completes while the loop is idle."""
        pass

    def on_immediate_notification(self, text: str):
        """
        Called for immediate notifications (high/urgent priority tasks).
        Should display prominently and potentially interrupt.
        """
        pass

    def on_idle_notification(self, task: BackgroundTask):
        """
        Called when in idle/wake-word mode and a task completes.
        Should activate the assistant briefly to deliver notification.
        """
        pass


def run_response_loop(handler: ResponseLoopHandler):
    """
    Shared response generation loop (runs in a background daemon thread).

    ENHANCED: Now handles conversation-aware background task notifications
    for all three cases using the NotificationDecisionEngine.

    Waits for *main_runner* to be set (triggered by ``add_message``), then:
    1. Generates an LLM response
    2. Handles tool calls if present, otherwise speaks the text response
    3. Handles ``[stop_chat]`` markers
    4. Calls ``on_processing_end()`` so the host can update UI state
    5. Tracks conversation state for notification decisions

    While idle (every 500 ms), checks for completed background tasks and
    uses the decision engine to determine the best notification strategy:
    - Case 1: Inject into next response if same conversation is active
    - Case 2: Queue for wake-word mode if no conversation active
    - Case 3: Defer or notify based on relevance if different conversation active
    """
    # Initialize state tracking
    decision_engine = NotificationDecisionEngine()
    state_manager = ConversationStateManager()
    
    # Set initial mode based on environment (can be updated by caller)
    import os
    start_mode = os.getenv("AVA_START_MODE", "tui_text")
    if "tui" in start_mode:
        set_mode("tui")
    elif start_mode == "vosk":
        set_mode("wakeword")
    elif start_mode == "continuous":
        set_mode("continuous")
    else:
        set_mode("text")
    
    while True:
        # Efficient wait: return True immediately when main_runner is set,
        # or return False after 500ms (used to poll the completion queue).
        signaled = main_runner.wait(timeout=0.5)

        # ── Enhanced Idle Processing with Conversation Awareness ────────────
        if not main_runner.is_set():
            completion_queue = CompletionQueue()
            
            # Check for completed background tasks
            if completion_queue.has_notifications():
                completed_tasks = completion_queue.pop_all()
                
                for task in completed_tasks:
                    # Use decision engine to determine notification strategy
                    strategy = decision_engine.decide_notification_strategy(task)
                    
                    # Clear background task flag if this is a background task completion
                    if task.task_type != "general":
                        from core.AppStates import background_task_active
                        background_task_active.clear()
                    
                    if strategy.delivery_method == DeliveryMethod.IMMEDIATE:
                        # Immediate notification (interrupt if allowed)
                        if strategy.interrupt_current:
                            handler.on_immediate_notification(strategy.notification_text)
                        else:
                            # Queue for next opportunity
                            completion_queue.store_pending(strategy.notification_text)
                            
                    elif strategy.delivery_method == DeliveryMethod.NEXT_RESPONSE:
                        # Inject into conversation context for LLM to pick up
                        add_message(
                            role="system",
                            content=f"[BACKGROUND TASK UPDATE] {strategy.notification_text}",
                            tool_id="",
                            trigger_response=False,
                        )
                        # Mark as notified in registry
                        registry = TaskRegistry()
                        registry.mark_as_notified(task.id)
                        
                    elif strategy.delivery_method == DeliveryMethod.QUEUE_FOR_IDLE:
                        # Store for wake-word mode notification
                        completion_queue.store_for_idle(task)
                        
                    # DEFER: Do nothing now, will be picked up later
                    # The task remains in the queue for future consideration
                
                # Also check for legacy pending completions (backward compatibility)
                legacy_summary = check_and_format_completions()
                if legacy_summary:
                    completion_queue.store_pending(legacy_summary)
                    handler.on_background_task_notify(legacy_summary)
                    add_message(
                        role="user",
                        content=f"[SYSTEM NOTIFICATION] {legacy_summary}",
                        tool_id="",
                        trigger_response=False,
                    )
            
            # Update idle state
            if state_manager.current_mode in ["wakeword", "idle"]:
                if completion_queue.has_idle_notifications():
                    idle_notifications = completion_queue.get_idle_notifications()
                    for task in idle_notifications:
                        handler.on_idle_notification(task)
            
            continue

        # ── Process a new message ───────────────────────────────────────
        if main_runner.is_set():
            main_running.set()
            main_runner.clear()
            
            # Update conversation state - a new message means conversation is active
            state_manager.user_spoke()
            state_manager.is_conversation_active = True
            
            try:
                response = generate_response()
                tool_calls = response.get("tool_calls")

                if tool_calls:
                    for tool_call in tool_calls:
                        # Support both OpenAI SDK objects and plain dicts
                        if isinstance(tool_call, dict):
                            tool_id = tool_call.get("id")
                            function_payload = tool_call.get("function", {}) or {}
                            args_raw = function_payload.get("arguments", "{}")
                            func_name = function_payload.get("name")
                        else:
                            tool_id = tool_call.id
                            args_raw = tool_call.function.arguments
                            func_name = tool_call.function.name

                        # Parse arguments (JSON string or already a dict)
                        if isinstance(args_raw, str):
                            try:
                                args = (
                                    json.loads(args_raw)
                                    if args_raw.strip()
                                    else {}
                                )
                            except json.JSONDecodeError:
                                args = args_raw
                        elif isinstance(args_raw, dict):
                            args = args_raw
                        else:
                            args = {}

                        if func_name == "inform_user_between_tool_calls":
                            if isinstance(args, dict):
                                arg_text = args.get(
                                    "message", args.get("text", str(args))
                                )
                            else:
                                arg_text = str(args)

                            handler.speak(arg_text)
                            # Update state: assistant spoke
                            state_manager.assistant_spoke()
                            # Check for work-in-progress
                            if _is_work_in_progress(arg_text):
                                work_in_progress.set()
                                expecting_followup.set()
                            add_message(
                                role="tool",
                                content="informed the user",
                                tool_id=tool_id,
                            )
                        else:
                            # Notify UI that a tool call is starting
                            handler.on_tool_call_start(func_name, args)
                            # Tool calls indicate work in progress - set flags BEFORE execution
                            # This ensures the conversation stays open while the tool runs
                            work_in_progress.set()
                            expecting_followup.set()
                            print(f"[ResponseLoop] Tool call starting: {func_name}, setting work_in_progress")
                            
                            # Execute the tool
                            func_resp, t_id = handle_tool_call(tool_call)
                            # Notify UI of the result
                            handler.on_tool_call_result(
                                str(func_resp) if func_resp else "done"
                            )
                            # Update state: tool execution is part of assistant activity
                            state_manager.assistant_spoke()
                            
                            add_message(
                                role="tool",
                                content=func_resp,
                                tool_id=t_id,  # type: ignore
                            )
                else:
                    # Direct text response
                    content = response.get("content")
                    if content:
                        # [stop_chat] is prompted to appear as the last line
                        stripped_lower = content.lower().strip()
                        should_stop = stripped_lower.endswith("[stop_chat]")
                        if should_stop:
                            clean = (
                                content.replace("[stop_chat]", "")
                                .replace("[STOP_CHAT]", "")
                                .strip()
                            )
                            if clean:
                                handler.speak(clean)
                                state_manager.assistant_spoke()
                            else:
                                handler.speak("Going silent.")
                                state_manager.assistant_spoke()
                            stop_chat_flag.set()
                            print("stop flag set")
                        else:
                            handler.speak(content)
                            state_manager.assistant_spoke()
                            # Check for work-in-progress in direct responses
                            if _is_work_in_progress(content):
                                work_in_progress.set()
                                expecting_followup.set()
                                print(f"[ResponseLoop] Work in progress detected: {content[:50]}...")
                            else:
                                # Clear work-in-progress if response is complete
                                if work_in_progress.is_set():
                                    work_in_progress.clear()
                                    expecting_followup.clear()
                                    print("[ResponseLoop] Work completed, clearing flags")
                    else:
                        handler.speak("I'm not sure how to respond to that.")
                        state_manager.assistant_spoke()
                        # Clear any work-in-progress flags on error/unknown
                        work_in_progress.clear()
                        expecting_followup.clear()
            finally:
                main_running.clear()
                handler.on_processing_end()
