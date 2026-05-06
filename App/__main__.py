# # ── Mode selection (must be before gevent import) ────────────────────────────
# START_MODE = "tui"  # Options: "continuous", "vosk", "text", "tui"

# # Skip gevent monkey-patching for TUI mode — it breaks Textual's threading
# if START_MODE != "tui":
#     from gevent import monkey
#     monkey.patch_all(thread=False)
import json
import os
import random
import sys
import threading
import time
import wave

from config import ASSISTANT_NAME, USER_NAME
from core.AppStates import main_runner, stop_event
from core.FuncHandler import handle_tool_call
from core.generate import generate_response
from core.messageHandler import add_message, reset_messages
from core.server_api import list_remote_conversations
from core.TaskManager import CompletionQueue, check_and_format_completions
from knowledge.ConversationManager import (
    save_current_conversation,
    start_new_conversation,
)
from utils import chime, tts_piper

# ── Terminal colors ──────────────────────────────────────────────────────────
COLOR_RESET = "\033[0m"
COLOR_GREEN = "\033[92m"
COLOR_YELLOW = "\033[93m"
COLOR_CYAN = "\033[96m"

# Commands that end the conversation
SHUTUP_COMMANDS = {"shutup", "shut up", "exit", "quiet", "stop", "bye", "goodbye"}
EXIT_RESPONSES = [
    "Goodbye, sir.",
    "Alright, I'll be quiet.",
    "Got it, stepping back!",
    "I'll give you some space.",
    "Okay, I'll stop talking now.",
    "Message received, going silent.",
    "As you wish, sir!",
    "No problem, I'll leave you alone.",
    "Alright, I'll go away now.",
    "Sorry if I bothered you, I'll stop.",
]
_settings_path = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "settings.json"
)
try:
    with open(_settings_path, "r") as _f:
        os.environ.update({k: str(v) for k, v in json.load(_f).items()})
except Exception:
    pass

# Event to track whether main() is actively processing a response
main_running = threading.Event()


# ── Terminal output helpers ──────────────────────────────────────────────────


def _print_user_block(text: str):
    """Print user input with green barriers."""
    print(f"\n{COLOR_GREEN}{'-' * 65}{COLOR_RESET}")
    print(f"{COLOR_GREEN}{text}{COLOR_RESET}")
    print(f"{COLOR_GREEN}{'-' * 65}{COLOR_RESET}\n")


def _print_assistant_block(text: str):
    """Print assistant output with cyan barriers and yellow typing animation."""
    print(f"\n{COLOR_CYAN}{'=' * 65}{COLOR_RESET}")
    print(COLOR_YELLOW, end="")
    for char in text:
        print(char, end="", flush=True)
        time.sleep(0.001)
    print(COLOR_RESET)
    print(f"{COLOR_CYAN}{'=' * 65}{COLOR_RESET}\n")


def _wait_for_response_complete():
    """Block until the main response loop finishes processing."""
    main_running.wait(timeout=5.0)
    while main_running.is_set():
        time.sleep(0.01)


# ── Volume control ───────────────────────────────────────────────────────────


def _adjust_session_volumes(multiplier: float, label: str):
    """Multiply all non-Python audio session volumes by `multiplier` (Windows only)."""
    try:
        from comtypes import CoInitialize
        from pycaw.pycaw import AudioUtilities, ISimpleAudioVolume

        CoInitialize()
        for session in AudioUtilities.GetAllSessions():
            if session.Process and session.Process.name() != "python.exe":
                vol = session._ctl.QueryInterface(ISimpleAudioVolume)
                current = vol.GetMasterVolume()
                new = max(0.0, min(1.0, current * multiplier))
                vol.SetMasterVolume(new, None)
        print(f"[Main] {label}")
    except Exception as e:
        print(f"[WARN] Volume control failed: {e}")


def duck_volume():
    """Lower all active audio sessions to 20% of current level."""
    _adjust_session_volumes(0.2, "Volume ducked to 20% for conversation")


def unduck_volume():
    """Restore all audio sessions to original levels."""
    _adjust_session_volumes(5.0, "Volume restored to normal levels")


# ── Core helpers ─────────────────────────────────────────────────────────────


def get_duration_wave(file_path, timeout=15.0):
    """
    Wait for a WAV file to appear on disk and return its duration in seconds.
    Used to estimate how long the TTS response will play, so we know how long
    to keep the microphone open for continued conversation.

    Returns 0.0 if file not found within timeout or unreadable.
    """
    start = time.time()
    while not os.path.exists(file_path):
        if timeout is not None and (time.time() - start) > timeout:
            return 0.0
        time.sleep(0.05)
    try:
        with wave.open(file_path, "rb") as audio_file:
            frame_rate = audio_file.getframerate() or 1
            n_frames = audio_file.getnframes()
            return n_frames / float(frame_rate)
    except Exception:
        return 0.0


def cleanup_false_detection():
    """Clean up false wake word detection from terminal"""
    sys.stdout.write("\033[K")  # Clear current line
    sys.stdout.flush()


def speak(text):
    """
    Print text with a typing animation and play TTS audio in a background thread.
    Sets stop_event to interrupt any currently playing audio first.
    """
    stop_event.set()
    _print_assistant_block(f"{ASSISTANT_NAME.upper()}: " + text)
    stop_event.clear()

    if "text" not in START_MODE:
        speak_thread = threading.Thread(
            target=tts_piper.speak, args=(text, stop_event), daemon=True
        )
        speak_thread.start()


# ── Main response loop ───────────────────────────────────────────────────────


def main():
    """
    Main response generation loop (runs in a background thread)

    Waits for main_runner to be set (triggered by add_message), then:
    1. Generates an LLM response
    2. Handles tool calls if present, or speaks the response

    Also periodically checks for completed background tasks and announces them.

    Uses Event.wait(timeout=0.5) instead of time.sleep(0.1) polling —
    responds to messages near-instantly while still checking completions every 500ms.
    """
    while True:
        # Efficient wait: returns True immediately when main_runner is set,
        # or returns False after 500ms timeout (used to check completion queue)
        signaled = main_runner.wait(timeout=0.5)

        if not main_runner.is_set():
            # Timed out — check for completed background tasks while idle
            completion_queue = CompletionQueue()
            if completion_queue.has_notifications():
                completed_summary = check_and_format_completions()
                if completed_summary:
                    # Inject background task results as a system notification
                    add_message(
                        role="user",
                        content=f"[SYSTEM NOTIFICATION] {completed_summary}",
                        tool_id="",
                    )
            continue

        # main_runner was set — a new message is ready for processing
        if main_runner.is_set():
            main_running.set()
            main_runner.clear()
            try:
                response = generate_response()
                tool_calls = response.get("tool_calls")
                if tool_calls:
                    tool_call = tool_calls[0]
                    if isinstance(tool_call, dict):
                        tool_id = tool_call.get("id")
                        function_payload = tool_call.get("function", {}) or {}
                        args_raw = function_payload.get("arguments", "{}")
                        func_name = function_payload.get("name")
                    else:
                        tool_id = tool_call.id
                        args_raw = tool_call.function.arguments
                        func_name = tool_call.function.name

                    if func_name == "inform_user_between_tool_calls":
                        if isinstance(args_raw, str):
                            try:
                                args = json.loads(args_raw) if args_raw.strip() else {}
                            except json.JSONDecodeError:
                                args = args_raw
                        elif isinstance(args_raw, dict):
                            args = args_raw
                        else:
                            args = {}

                        if isinstance(args, dict):
                            # The argument might be a key in a JSON object
                            arg_text = args.get("message", args.get("text", str(args)))
                        else:
                            # Or it may simply be the raw string
                            arg_text = str(args)

                        speak(arg_text)
                        # Add a tool response back into the conversation
                        add_message(
                            role="tool", content="informed the user", tool_id=tool_id
                        )
                    else:
                        # Execute the tool and feed its result back into the conversation
                        func_resp, t_id = handle_tool_call(tool_call)
                        add_message(role="tool", content=func_resp, tool_id=t_id)  # type: ignore
                else:
                    # Direct text response — speak it
                    content = response.get("content")
                    if content:
                        speak(content)
                    else:
                        speak("I'm not sure how to respond to that.")
            finally:
                # Always clear main_running, even if an error occurred
                main_running.clear()


# ── Wake / Input Modes ───────────────────────────────────────────────────────


def voice_mode_continuous():
    """
    Continuous wake mode: always listening, transcribes everything,
    checks if a wake word is in the transcription.

    Flow:
    1. Record ambient audio until voice is detected
    2. Transcribe with Whisper and check for wake word ("ava", "assistant", etc.)
    3. If wake word found, enter conversation loop:
       a. Send transcription to LLM
       b. Wait for response (Event.wait for start, short poll for finish)
       c. Listen for continued conversation (timeout = TTS duration + 7s)
       d. If user speaks again, continue; otherwise end conversation
    4. Save conversation and go back to listening
    """
    from utils import stt_hybrid as stt
    from utils import tts_piper

    tts_piper.init_tts()
    print(f"[{ASSISTANT_NAME.upper()}] Ready.")

    recorder = stt.VoiceRecorder()
    while True:
        # Phase 1: Listen for any speech
        success, filename = recorder.record()
        if success:
            # Get Whisper transcription (background thread started at end of record())
            prompt = recorder.get_whisper_result(timeout=15)
            transcription = prompt if prompt is not None else ""

            # Check for wake words
            if any(
                word in transcription.lower()
                for word in ["ava", "assistant", "eva", "ayva", "evaa"]
            ):
                # Clear any previous false detection output and show wake word detected
                cleanup_false_detection()
                print(f"[{ASSISTANT_NAME.upper()}] Wake word detected!")

                # Wake word detected — start a new conversation thread
                start_new_conversation()
                reset_messages()
                duck_volume()

                while True:
                    # Wait for any ongoing response to finish before proceeding
                    while main_runner.is_set() or main_running.is_set():
                        time.sleep(0.01)

                    transcription = f"{USER_NAME}: " + transcription
                    _print_user_block(transcription)

                    # Check for exit commands
                    if any(cmd in transcription.lower() for cmd in SHUTUP_COMMANDS):
                        print(
                            f"{COLOR_YELLOW}{ASSISTANT_NAME.upper()}: {random.choice(EXIT_RESPONSES)}{COLOR_RESET}"
                        )
                        break
                    else:
                        # Send user message to the LLM
                        add_message(role="user", content=transcription, tool_id="")

                        # Wait for main() to finish generating the response + TTS
                        _wait_for_response_complete()

                        # Measure TTS audio duration to set continued conversation timeout
                        # (give user speech_duration + 7 seconds to respond)
                        timeout = tts_piper.get_last_duration() + 7

                        # Listen for continued conversation
                        # Reduced silence_duration from 4.0s → 2.5s for faster turnaround
                        continued_convo, filename = recorder.record(
                            timeout=timeout, silence_duration=2.5
                        )
                        if continued_convo:
                            stop_event.clear()
                            transcription = str(recorder.get_whisper_result(timeout=15))
                            continue
                        else:
                            stop_event.clear()
                            break

                # Conversation ended — save before going back to listening
                unduck_volume()
                save_current_conversation()
                print(
                    f"\n[{ASSISTANT_NAME.upper()}] Conversation ended. Listening for wake word..."
                )
            else:
                # Clear the false detection line without clearing the whole screen
                cleanup_false_detection()


def voice_mode_wakeword():
    """
    Vosk wake mode: lightweight wake word detection using local Vosk model.
    Only activates full Whisper transcription after wake word is heard.
    Each wake word starts a NEW conversation thread.

    Uses less CPU than continuous mode when idle, since Vosk is much lighter
    than recording + Whisper for every spoken phrase.
    """
    from utils import stt_hybrid as stt
    from utils import tts_piper
    from utils.wakeword import WakeWordDetector

    tts_piper.init_tts()
    print(f"[{ASSISTANT_NAME.upper()}] Ready.")

    print(f"[{ASSISTANT_NAME.upper()}] Initializing wake word detector...")
    try:
        detector = WakeWordDetector()
    except FileNotFoundError as e:
        print(f"[ERROR] {e}")
        print(
            f"[{ASSISTANT_NAME.upper()}] Falling back to continuous listening mode..."
        )
        voice_mode_continuous()
        return

    recorder = stt.VoiceRecorder()

    print(
        f"[{ASSISTANT_NAME.upper()}] Ready. Say '{ASSISTANT_NAME}', 'Ava', or 'Assistant' to wake me up."
    )

    while True:
        # Phase 1: Wait for wake word (low CPU — Vosk only)
        print(f"[{ASSISTANT_NAME.upper()}] Listening... ", end="")
        sys.stdout.flush()
        detector.listen_for_wakeword()
        # Clear the "Listening..." text
        sys.stdout.write("\033[K\r")  # Clear line and move cursor to start
        sys.stdout.flush()
        # play startup chime
        chime.play()
        # Wake word detected — start a NEW conversation thread
        start_new_conversation()
        reset_messages()
        duck_volume()

        print(f"[{ASSISTANT_NAME.upper()}] Yes, sir?")

        # Phase 2: Listen for the actual command
        success, filename = recorder.record(timeout=5)

        if not success:
            print(
                f"[{ASSISTANT_NAME.upper()}] I didn't catch that. Going back to sleep."
            )
            unduck_volume()
            save_current_conversation()
            detector.reset()
            continue

        # Use background Whisper (already started by recorder.record())
        # instead of calling stt.transcribe_whisper() again (avoids duplicate work)
        transcription = recorder.get_whisper_result(timeout=15) or ""

        if not transcription.strip():
            print(
                f"[{ASSISTANT_NAME.upper()}] I didn't hear anything. Going back to sleep."
            )
            unduck_volume()
            save_current_conversation()
            detector.reset()
            continue

        # Phase 3: Conversation loop
        while True:
            if not main_runner.is_set():
                full_transcription = f"{USER_NAME}: " + transcription
                _print_user_block(full_transcription)

                # Check for exit commands
                if any(cmd in transcription.lower() for cmd in SHUTUP_COMMANDS):
                    response = random.choice(EXIT_RESPONSES)
                    speak(response)
                    break

                # Send to LLM
                add_message(role="user", content=full_transcription, tool_id="")

                # Freeze threshold adaptation while TTS plays — speaker bleed through
                # the mic would otherwise inflate the noise floor and make the user's
                # next speech appear quieter than the threshold
                recorder.freeze_noise_floor = True
                # Wait for response to complete
                _wait_for_response_complete()
                recorder.freeze_noise_floor = False

                # Listen for continued conversation
                timeout = tts_piper.get_last_duration() + 7
                continued_convo, filename = recorder.record(timeout=timeout)

                if continued_convo:
                    stop_event.clear()
                    # Use background Whisper (already started by recorder.record())
                    transcription = recorder.get_whisper_result(timeout=15) or ""
                    if transcription.strip():
                        continue
                    else:
                        break
                else:
                    stop_event.clear()
                    break

        # Conversation ended — save and go back to wake word listening
        unduck_volume()
        save_current_conversation()
        detector.reset()
        print(
            f"[{ASSISTANT_NAME.upper()}] Going back to sleep. Say my name when you need me."
        )


def text_mode():
    """
    Text input mode for testing without a microphone.
    Type your message to start a conversation.
    Useful for debugging LLM responses and tool calls.
    """
    print(
        f"[{ASSISTANT_NAME.upper()}] Text mode. Type '{ASSISTANT_NAME.lower()}' followed by your command."
    )

    while True:
        transcription = input("You: ").strip()
        if not transcription:
            continue

        # Debug commands (checked before entering conversation loop)
        if transcription.lower() == "print messages":
            from core.messageHandler import messages

            print(messages)
            continue
        elif transcription.lower() == "print memories":
            from knowledge.memory import retrieve_memories

            print(retrieve_memories())
            continue
        elif transcription.lower() == "print conversations":
            remote_conversations = list_remote_conversations(limit=20)
            if remote_conversations:
                for conv in remote_conversations:
                    conv_id = conv.get("id", "")
                    name = conv.get("name", conv_id)
                    summary = conv.get("summary", "No summary")
                    print(f"- {name}: {summary}")
            else:
                from knowledge.ConversationManager import get_manager

                mgr = get_manager()
                for conv_id, info in mgr.conversations_index.items():
                    print(
                        f"- {info.get('name', conv_id)}: {info.get('summary', 'No summary')}"
                    )
            continue

        # Start a new conversation thread
        start_new_conversation()
        reset_messages()

        while True:
            # Check for exit commands on raw input
            if any(cmd in transcription.lower() for cmd in SHUTUP_COMMANDS):
                response = random.choice(EXIT_RESPONSES)
                print(response)
                break

            # Send to AI (add user name prefix for context)
            user_msg = f"{USER_NAME}: {transcription}"
            add_message(role="user", content=user_msg, tool_id="")

            # Wait for the response to fully complete (Event-based, not sleep polling)
            _wait_for_response_complete()

            # Prompt for next input
            transcription = input("You: ").strip()
            if transcription:
                if not stop_event.is_set():
                    stop_event.set()
                continue
            break

        # Conversation ended — save it
        save_current_conversation()
        print(
            f"[{ASSISTANT_NAME.upper()}] Conversation saved. Say my name when you need me."
        )


# START_MODE = 'tui_continuous'
START_MODE = os.getenv("AVA_START_MODE", "tui_text")


def start():
    """
    Application entry point: starts the main response thread and
    activates the chosen wake mode (continuous, vosk, or text).
    """

    main_runner.clear()

    if "tui" in START_MODE:
        # TUI mode handles its own main loop — don't start the classic one
        from ui.tui import run_tui

        run_tui(start_mode=START_MODE.replace("tui_", ""))
        return

    # Start the main response loop in a background daemon thread
    threading.Thread(target=main, daemon=True).start()

    print(f"[{ASSISTANT_NAME.upper()}] Starting in '{START_MODE}' wake mode...")

    if START_MODE == "vosk":
        voice_mode_wakeword()
    elif START_MODE == "continuous":
        voice_mode_continuous()
    elif START_MODE == "text":
        text_mode()
    else:
        print(f"[ERROR] Unknown wake mode: {START_MODE}. Using 'continuous'.")
        voice_mode_continuous()


if __name__ == "__main__":
    start()
