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
import time

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
    update_conversation_state,
    set_speaking_state,
    set_listening_state,
    set_mode,
    user_activity_occurred,
    sync_background_task_flag,
)
from core.TaskManager import BackgroundTask, TaskStatus, DeliveryMethod, TaskPriority
from core.jobs.announce_queue import get_announce_queue


def _drain_clarification_wakes(handler: "ResponseLoopHandler", state_manager: ConversationStateManager):
    """Inject [TASK NEEDS INPUT] and wake main when chat is active."""
    try:
        from core.jobs.clarify import get_clarification_bridge

        bridge = get_clarification_bridge()
        if not bridge.has_wakes():
            return
        wakes = bridge.pop_wakes()
    except Exception as e:
        print(f"[ResponseLoop] Clarify wake poll failed: {e}")
        return

    idle_seconds = state_manager.get_idle_duration().total_seconds()
    chat_active = state_manager.is_conversation_active
    busy = state_manager.is_speaking or state_manager.is_listening
    idle_mode = (not chat_active) or state_manager.current_mode in ("wakeword", "idle")

    for req in wakes:
        # Mid-speech/listen: requeue for a later turn boundary.
        if busy:
            try:
                bridge.requeue_wake(req.id)
            except Exception:
                pass
            continue

        # Active conversation and not long-idle: wake main to self-answer or ask.
        if chat_active and idle_seconds < WAKE_IDLE_THRESHOLD_SECONDS:
            add_message(
                role="system",
                content=f"[TASK NEEDS INPUT]\n{req.to_compact_inject()}",
                tool_id="",
                trigger_response=True,
            )
            sync_background_task_flag()
            continue

        # Idle / asleep: desktop notify already fired; keep pending for next open.
        # Requeue so we can wake once the user is back in an active chat.
        try:
            bridge.requeue_wake(req.id)
        except Exception:
            pass
        if idle_mode or idle_seconds >= WAKE_IDLE_THRESHOLD_SECONDS:
            try:
                handler.on_background_task_notify(
                    f"Task needs input: {req.job_name or req.job_id}: {req.question}"
                )
            except Exception:
                pass


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

# Don't spontaneously wake the main agent when the user has been idle this
# long, even if their conversation is still flagged as active. The desktop
# toast + chime + inbox item cover the notification; the result is mentioned
# again on the next user turn.
WAKE_IDLE_THRESHOLD_SECONDS = 120


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


def _format_done(task: BackgroundTask) -> str:
    try:
        from core.jobs.notify import format_done_inject

        return format_done_inject(task)
    except Exception:
        return f"[BACKGROUND TASK DONE]\n{task.to_compact_done()}"


def _alert_once(task: BackgroundTask) -> None:
    try:
        from core.jobs.notify import alert_for_completed_task

        alert_for_completed_task(task)
    except Exception as e:
        print(f"[ResponseLoop] alert_for_completed_task failed: {e}")


def _enqueue_for_speak(
    task: BackgroundTask, *, already_alerted: bool = False
) -> None:
    """Queue a completion for spoken delivery (batch or serial via AnnounceQueue)."""
    get_announce_queue().enqueue(
        task, mode="speak", already_alerted=already_alerted
    )


def _wait_for_speech_idle(timeout: float = 120.0) -> None:
    """
    Block until current TTS finishes (or is interrupted / user starts new work).

    Critical for serial announce delivery: the next LLM turn must not call
    speak() while the previous utterance is still playing, or stop_event
    will cut the audio mid-sentence ("speech break after some time").
    """
    try:
        from utils import tts_piper
    except Exception:
        return

    if not tts_piper.is_playing():
        return

    deadline = time.time() + max(0.0, timeout)
    print("[ResponseLoop] Waiting for current speech to finish before next announce")
    while time.time() < deadline:
        # User kicked a new turn — let that take priority.
        if main_runner.is_set():
            return
        if not tts_piper.is_playing():
            return
        tts_piper.wait_until_done(timeout=0.15)


def _deliver_announce_batch(
    handler: "ResponseLoopHandler",
    *,
    wake: bool = True,
) -> int:
    """
    Inject the next announce-queue batch into chat history.

    Batch mode (idle): all pending tasks in one turn, single wake.
    Serial mode (after busy): one task per turn.
    Returns number of tasks injected.
    """
    aq = get_announce_queue()
    batch = aq.pop_delivery_batch()
    if not batch:
        return 0

    registry = TaskRegistry()
    print(
        f"[ResponseLoop] Announce deliver n={len(batch)} "
        f"remaining={aq.pending_count()}"
    )

    # Audible alert only if spawn-time alert_mode asked for sound (short/continuous).
    # Silent/notify defaults: main agent may call ping after DONE.
    if not batch[0].already_alerted:
        try:
            from core.TaskManager import alert_mode_wants_sound

            if alert_mode_wants_sound(getattr(batch[0].task, "alert_mode", None)):
                _alert_once(batch[0].task)
        except Exception:
            pass

    delivered = 0
    for i, item in enumerate(batch):
        task = item.task
        tid = str(getattr(task, "id", "") or "")
        # Skip if already spoken this process (or marked handled)
        try:
            if aq.was_announced(tid) and delivered > 0:
                continue
            meta = getattr(task, "metadata", None) or {}
            if meta.get("handled") or meta.get("announced"):
                continue
        except Exception:
            pass
        try:
            # TUI gets a short human line; LLM gets the compact DONE inject separately.
            ui_line = (
                task.to_ui_done()
                if hasattr(task, "to_ui_done")
                else task.to_compact_done()
            )
            handler.on_background_task_notify(ui_line)
        except Exception:
            pass
        add_message(
            role="system",
            content=_format_done(task),
            tool_id="",
            # Only the first inject wakes the LLM; rest ride in the same context.
            trigger_response=bool(wake) and delivered == 0,
        )
        try:
            registry.mark_as_notified(task.id)
            with registry._tasks_lock:
                live = registry._tasks.get(task.id)
                if live is not None:
                    live.metadata = dict(live.metadata or {})
                    live.metadata["announced"] = True
                    live.metadata["announced_at"] = __import__(
                        "datetime"
                    ).datetime.now().isoformat()
                    registry._persist_locked()
            aq.mark_announced(task.id)
        except Exception:
            pass
        delivered += 1
    return delivered


def _route_completed_tasks(
    completed_tasks: list,
    handler: "ResponseLoopHandler",
    state_manager: ConversationStateManager,
    decision_engine: NotificationDecisionEngine,
) -> None:
    """
    Route finished jobs: alerts + enqueue speak, or defer / idle-queue.

    Spoken delivery always goes through AnnounceQueue so concurrent timer +
    research completions cannot race the main agent.
    """
    completion_queue = CompletionQueue()
    registry = TaskRegistry()

    for task in completed_tasks:
        strategy = decision_engine.decide_notification_strategy(task)
        sync_background_task_flag()

        if strategy.delivery_method == DeliveryMethod.IMMEDIATE:
            # Hard alert only when alert_mode is short/continuous (real alarms).
            already = False
            try:
                from core.TaskManager import alert_mode_wants_sound

                if alert_mode_wants_sound(getattr(task, "alert_mode", None)):
                    _alert_once(task)
                    already = True
            except Exception:
                pass
            try:
                ui_line = (
                    task.to_ui_done()
                    if hasattr(task, "to_ui_done")
                    else (strategy.notification_text or task.to_compact_done())
                )
                handler.on_immediate_notification(ui_line)
            except Exception:
                pass
            _enqueue_for_speak(task, already_alerted=already)

        elif strategy.delivery_method == DeliveryMethod.NEXT_RESPONSE:
            # User is in an active conversation → always announce (speak).
            # Only hold while mid-speech/listen so we don't cut TTS.
            if state_manager.is_speaking or state_manager.is_listening:
                completion_queue.store_deferred(task)
            else:
                _enqueue_for_speak(task, already_alerted=False)

        elif strategy.delivery_method == DeliveryMethod.QUEUE_FOR_IDLE:
            # Away from chat: desktop toast already fired in TaskManager.
            # Keep a deferred entry so the *next user utterance* can include DONE.
            # Do NOT mark_as_notified here — that made research "silent forever".
            try:
                handler.on_idle_notification(task)
            except Exception:
                pass
            completion_queue.store_deferred(task)

        elif strategy.delivery_method == DeliveryMethod.DEFER:
            completion_queue.store_deferred(task)


class ResponseLoopHandler:
    """Base handler for the shared response loop.

    Subclasses override hook methods to customize UI behavior.
    The default no-op hooks are suitable for CLI mode.

    Hook methods (override as needed):
        speak(text)               - Display/speak assistant text
        on_processing_start()     - Response cycle beginning (UI spinners etc.)
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

    def on_processing_start(self):
        """Called when an LLM response cycle begins."""
        pass

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
        # or return False after 100ms (used to poll the completion queue).
        signaled = main_runner.wait(timeout=0.1)

        # ── Enhanced Idle Processing with Conversation Awareness ────────────
        if not main_runner.is_set():
            # Sub-agent clarifications: wake main or leave for next conversation.
            _drain_clarification_wakes(handler, state_manager)

            completion_queue = CompletionQueue()
            announce_q = get_announce_queue()

            # Finished jobs → route into announce queue / deferred / idle lists
            if completion_queue.has_notifications():
                completed_tasks = completion_queue.pop_all()
                _route_completed_tasks(
                    completed_tasks, handler, state_manager, decision_engine
                )

            # Deliver spoken announcements when main is free AND previous
            # speech has finished (so the next speak won't cut the last one).
            if not main_running.is_set() and announce_q.has_pending():
                try:
                    from utils import tts_piper

                    if tts_piper.is_playing():
                        continue
                except Exception:
                    pass
                # If we were busy earlier, serial mode is already set by mark_main_busy
                n = _deliver_announce_batch(handler, wake=True)
                if n:
                    # main_runner may already be set by add_message; loop will process
                    continue

            # Legacy idle list → move to deferred (same as QUEUE_FOR_IDLE path).
            # Never mark_as_notified without a real DONE inject / speak turn.
            if completion_queue.has_idle_notifications():
                for task in completion_queue.get_idle_notifications():
                    try:
                        handler.on_idle_notification(task)
                    except Exception:
                        pass
                    completion_queue.store_deferred(task)

            continue

        # ── Process a new message ───────────────────────────────────────
        if main_runner.is_set():
            main_running.set()
            main_runner.clear()
            announce_q = get_announce_queue()
            announce_q.mark_main_busy()
            try:
                handler.on_processing_start()
            except Exception:
                pass

            # Update conversation state - a new message means conversation is active
            state_manager.user_spoke()
            state_manager.is_conversation_active = True

            # Jobs that finished while away / mid-listen / process restart:
            # attach DONE to *this* user turn (user already woke us). Never
            # auto-start a turn without user input (see ConversationManager).
            try:
                from core.jobs.notify import flush_pending_announcements

                flush_pending_announcements(trigger_response=False)
            except Exception as e:
                print(f"[ResponseLoop] flush_pending_announcements failed: {e}")

            # Unfinished timer→WhatsApp/etc. must survive empty DONE replies and "ok"
            try:
                from core.TaskManager import inject_open_main_actions

                inject_open_main_actions(trigger_response=False)
            except Exception as e:
                print(f"[ResponseLoop] inject_open_main_actions failed: {e}")

            try:
                # Explicit multi-round agent loop (including after [BACKGROUND TASK DONE]).
                # Tool results are appended with trigger_response=False so we stay in this
                # turn and call generate again — not a fragile re-entry via main_runner.
                max_tool_rounds = int(os.getenv("AVA_MAX_TOOL_ROUNDS", "10"))
                max_tool_rounds = max(1, min(max_tool_rounds, 20))
                turn_tools_called = False
                turn_spoke = False
                main_action_nudges = 0

                for _round in range(max_tool_rounds):
                    response = generate_response()
                    if response.get("error"):
                        content = response.get("content") or (
                            "I hit an error processing that."
                        )
                        handler.speak(content)
                        state_manager.assistant_spoke()
                        turn_spoke = True
                        work_in_progress.clear()
                        expecting_followup.clear()
                        break

                    tool_calls = response.get("tool_calls") or []
                    content = response.get("content")

                    # Model tried to soft-ack while main_action still open — force tools
                    if (
                        response.get("_main_action_needs_tools")
                        and not tool_calls
                        and main_action_nudges < 2
                    ):
                        main_action_nudges += 1
                        action = response.get("_main_action_text") or "the pending action"
                        print(
                            f"[ResponseLoop] Forcing main_action tool round "
                            f"({main_action_nudges}/2): {str(action)[:80]!r}"
                        )
                        add_message(
                            role="system",
                            content=(
                                "[SYSTEM — TOOLS REQUIRED NOW]\n"
                                f"You must call tools to complete: {action}\n"
                                "Use web (weather), get_time_date, whatsapp_plugin, etc. "
                                "as needed. Reply with tool_calls only — no bare text ack."
                            ),
                            tool_id="",
                            trigger_response=False,
                        )
                        continue

                    if tool_calls:
                        turn_tools_called = True
                        # Optional spoken preamble while tools run (same assistant turn)
                        if (
                            content
                            and not response.get("_duplicate_assistant")
                            and str(content).strip()
                        ):
                            handler.speak(str(content).strip())
                            state_manager.assistant_spoke()
                            turn_spoke = True

                        for tool_call in tool_calls:
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
                                state_manager.assistant_spoke()
                                turn_spoke = True
                                if _is_work_in_progress(arg_text):
                                    work_in_progress.set()
                                    expecting_followup.set()
                                add_message(
                                    role="tool",
                                    content="informed the user",
                                    tool_id=tool_id,
                                    trigger_response=False,
                                )
                            else:
                                handler.on_tool_call_start(func_name, args)
                                work_in_progress.set()
                                expecting_followup.set()
                                print(
                                    f"[ResponseLoop] Tool call starting: {func_name} "
                                    f"(round {_round + 1}/{max_tool_rounds})"
                                )

                                func_resp, t_id = handle_tool_call(tool_call)
                                handler.on_tool_call_result(
                                    str(func_resp) if func_resp else "done"
                                )
                                state_manager.assistant_spoke()

                                add_message(
                                    role="tool",
                                    content=func_resp,
                                    tool_id=t_id,  # type: ignore
                                    trigger_response=False,
                                )
                        # Continue: generate again with tool results in context
                        continue

                    # Final text-only response (no more tools this turn)
                    # Skip soft acks when main_action still open (after nudges exhausted)
                    if response.get("_main_action_needs_tools") and not tool_calls:
                        action = response.get("_main_action_text") or "pending action"
                        fail_line = (
                            f"I still need to finish this: {action}. "
                            "Trying tools now failed to start — please say go again."
                        )
                        handler.speak(fail_line)
                        state_manager.assistant_spoke()
                        turn_spoke = True
                        break

                    if response.get("_duplicate_assistant"):
                        print(
                            "[ResponseLoop] Dropping duplicate assistant speech"
                        )
                    elif content:
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
                                turn_spoke = True
                            else:
                                handler.speak("Going silent.")
                                state_manager.assistant_spoke()
                                turn_spoke = True
                            stop_chat_flag.set()
                            print("stop flag set")
                        else:
                            handler.speak(content)
                            state_manager.assistant_spoke()
                            turn_spoke = True
                            if _is_work_in_progress(content):
                                work_in_progress.set()
                                expecting_followup.set()
                                print(
                                    f"[ResponseLoop] Work in progress detected: "
                                    f"{content[:50]}..."
                                )
                            else:
                                if work_in_progress.is_set():
                                    work_in_progress.clear()
                                    expecting_followup.clear()
                                    print(
                                        "[ResponseLoop] Work completed, clearing flags"
                                    )
                    else:
                        handler.speak("I'm not sure how to respond to that.")
                        state_manager.assistant_spoke()
                        turn_spoke = True
                        work_in_progress.clear()
                        expecting_followup.clear()

                    # After speaking a DONE outcome, fully ack + redact so the
                    # same completion cannot wake the model again this session.
                    try:
                        from core.TaskManager import auto_acknowledge_after_turn

                        early = auto_acknowledge_after_turn(
                            tools_called=turn_tools_called,
                            spoke=turn_spoke,
                        )
                        if early:
                            print(
                                f"[ResponseLoop] Early ack after DONE speak: {early}"
                            )
                    except Exception as e:
                        print(f"[ResponseLoop] Early ack failed: {e}")
                    break
                else:
                    print(
                        f"[ResponseLoop] Hit max tool rounds ({max_tool_rounds}); stopping turn"
                    )
                    work_in_progress.clear()
                    expecting_followup.clear()

                # Short jobs (timers + main_action): auto-dismiss after this turn
                # so the next message does not re-litigate the same DONE inject.
                try:
                    from core.TaskManager import auto_acknowledge_after_turn

                    acked = auto_acknowledge_after_turn(
                        tools_called=turn_tools_called,
                        spoke=turn_spoke,
                    )
                    if acked:
                        print(
                            f"[ResponseLoop] Auto-acknowledged short completions: {acked}"
                        )
                except Exception as e:
                    print(f"[ResponseLoop] auto_acknowledge_after_turn failed: {e}")
            finally:
                main_running.clear()
                try:
                    handler.on_processing_end()
                except Exception:
                    pass
                # Free announce queue; if more serial items wait, inject next + wake
                announce_q.mark_main_idle()
                # Also pull any completions that arrived mid-turn
                if completion_queue.has_notifications():
                    _route_completed_tasks(
                        completion_queue.pop_all(),
                        handler,
                        state_manager,
                        decision_engine,
                    )
                # Wait for this turn's TTS to finish before starting the next
                # announce turn — otherwise speak() stop_event chops audio.
                if announce_q.has_pending() and not main_runner.is_set():
                    _wait_for_speech_idle()
                if announce_q.has_pending() and not main_runner.is_set():
                    _deliver_announce_batch(handler, wake=True)
