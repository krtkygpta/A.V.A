import random
import os
import wave
import threading
from config import USER_NAME, ASSISTANT_NAME
from core.generate import generate_response
from core.messageHandler import add_message, reset_messages
import time
from core.AppStates import main_runner, stop_event
from core.FuncHandler import handle_tool_call
from utils import stt_hybrid as stt, tts
from utils.tts import SPEECH_FILE
from core.TaskManager import CompletionQueue, check_and_format_completions
from knowledge.ConversationManager import start_new_conversation, save_current_conversation

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
    "Sorry if I bothered you, I'll stop."
]

# Event to track whether main() is actively processing a response
main_running = threading.Event()

def speak(text):
    """
    Print text with a typing animation and play TTS audio in a background thread.
    Sets stop_event to interrupt any currently playing audio first.
    """
    stop_event.set()
    def animate_and_speak(string):
        for char in string:
            print(char, end="", flush=True)
            time.sleep(0.001)
        print(" ")

    animate_and_speak(f"{ASSISTANT_NAME.upper()}: " + text)
    stop_event.clear()
    speak_thread = threading.Thread(target=tts.run_tts_command, args=(text, stop_event), daemon=True)
    speak_thread.start()

def main():
    """
    Main response generation loop (runs in a background thread).

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
                        role='user',
                        content=f"[SYSTEM NOTIFICATION] {completed_summary}",
                        tool_id=''
                    )
            continue
            
        # main_runner was set — a new message is ready for processing
        if main_runner.is_set():
            main_running.set()
            main_runner.clear()
            try:
                response = generate_response()
                tool_calls = response.get('tool_calls')
                if tool_calls:
                    # Execute the tool and feed its result back into the conversation
                    func_resp, tool_id = handle_tool_call(tool_calls[0])
                    add_message(role='tool', content=func_resp, tool_id=tool_id)  # type: ignore
                else:
                    # Direct text response — speak it
                    speak(response.get('content'))
            finally:
                # Always clear main_running, even if an error occurred
                main_running.clear()

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
        with wave.open(file_path, 'rb') as audio_file:
            frame_rate = audio_file.getframerate() or 1
            n_frames = audio_file.getnframes()
            return n_frames / float(frame_rate)
    except Exception:
        return 0.0


def wake():
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
    recorder = stt.VoiceRecorder()
    while True:
        # Phase 1: Listen for any speech
        success, filename = recorder.record()
        if success:
            # Get Whisper transcription (background thread started at end of record())
            prompt = recorder.get_whisper_result(timeout=15)
            transcription = prompt if prompt is not None else ""

            # Check for wake words
            if any(word in transcription.lower() for word in ["ava", "assistant", "eva", "ayva", "evaa"]):
                # Wake word detected — start a new conversation thread
                start_new_conversation()
                reset_messages()

                while True:
                    # Wait for any ongoing response to finish before proceeding
                    while main_runner.is_set() or main_running.is_set():
                        time.sleep(0.01)

                    transcription = f"{USER_NAME}: " + transcription
                    print(transcription)

                    # Check for exit commands
                    if any(cmd in transcription.lower() for cmd in SHUTUP_COMMANDS):
                        print(random.choice(EXIT_RESPONSES))
                        break
                    else:
                        # Send user message to the LLM
                        add_message(role="user", content=transcription, tool_id='')

                        # Wait for main() to pick up the message and start processing
                        # Event.wait() returns instantly when set (vs. 10ms sleep poll delay)
                        main_running.wait(timeout=5.0)

                        # Wait for main() to finish generating the response + TTS
                        while main_running.is_set():
                            time.sleep(0.01)

                        # Measure TTS audio duration to set continued conversation timeout
                        # (give user speech_duration + 7 seconds to respond)
                        timeout = get_duration_wave(SPEECH_FILE) + 7

                        # Listen for continued conversation
                        # Reduced silence_duration from 4.0s → 2.5s for faster turnaround
                        continued_convo, filename = recorder.record(timeout=timeout, silence_duration=2.5)
                        if continued_convo:
                            stop_event.clear()
                            transcription = str(recorder.get_whisper_result(timeout=15))
                            continue
                        else:
                            stop_event.clear()
                            break

                # Conversation ended — save before going back to listening
                save_current_conversation()

                
def wake_vosk():
    """
    Vosk wake mode: lightweight wake word detection using local Vosk model.
    Only activates full Whisper transcription after wake word is heard.
    Each wake word starts a NEW conversation thread.

    Uses less CPU than continuous mode when idle, since Vosk is much lighter
    than recording + Whisper for every spoken phrase.
    """
    from utils.wakeword import WakeWordDetector
    
    print(f"[{ASSISTANT_NAME.upper()}] Initializing wake word detector...")
    try:
        detector = WakeWordDetector()
    except FileNotFoundError as e:
        print(f"[ERROR] {e}")
        print(f"[{ASSISTANT_NAME.upper()}] Falling back to continuous listening mode...")
        wake()
        return
    
    recorder = stt.VoiceRecorder()
    
    print(f"[{ASSISTANT_NAME.upper()}] Ready. Say '{ASSISTANT_NAME}', 'Ava', or 'Assistant' to wake me up.")
    
    while True:
        # Phase 1: Wait for wake word (low CPU — Vosk only)
        detector.listen_for_wakeword()
        
        # Wake word detected — start a NEW conversation thread
        start_new_conversation()
        reset_messages()
        
        print(f"[{ASSISTANT_NAME.upper()}] Yes, sir?")
        
        # Phase 2: Listen for the actual command
        success, filename = recorder.record(timeout=5)
        
        if not success:
            print(f"[{ASSISTANT_NAME.upper()}] I didn't catch that. Going back to sleep.")
            save_current_conversation()
            detector.reset()
            continue
        
        transcription = stt.transcribe_whisper(filename)
        transcription = transcription if transcription else ""
        
        if not transcription.strip():
            print(f"[{ASSISTANT_NAME.upper()}] I didn't hear anything. Going back to sleep.")
            save_current_conversation()
            detector.reset()
            continue
        
        # Phase 3: Conversation loop
        while True:
            if not main_runner.is_set():
                full_transcription = f"{USER_NAME}: " + transcription
                print(full_transcription)
                
                # Check for exit commands
                if any(cmd in transcription.lower() for cmd in SHUTUP_COMMANDS):
                    response = random.choice(EXIT_RESPONSES)
                    print(f"{ASSISTANT_NAME.upper()}: {response}")
                    speak(response)
                    break
                
                # Send to LLM
                add_message(role="user", content=full_transcription, tool_id='')
                
                # Wait for response to complete
                while main_running.is_set():
                    time.sleep(0.01)
                
                # Listen for continued conversation
                timeout = get_duration_wave(SPEECH_FILE) + 7
                continued_convo, filename = recorder.record(timeout=timeout)
                
                if continued_convo:
                    stop_event.clear()
                    transcription = str(stt.transcribe_whisper(filename))
                    if transcription and transcription.strip():
                        continue
                    else:
                        break
                else:
                    stop_event.clear()
                    break
        
        # Conversation ended — save and go back to wake word listening
        save_current_conversation()
        detector.reset()
        print(f"[{ASSISTANT_NAME.upper()}] Going back to sleep. Say my name when you need me.")

def wake_temp():
    """
    Text input mode for testing without a microphone.
    Type 'ava' followed by your message to start a conversation.
    Useful for debugging LLM responses and tool calls.
    """
    print(f"[{ASSISTANT_NAME.upper()}] Text mode. Type '{ASSISTANT_NAME.lower()}' followed by your command.")
    
    while True:
        transcription = input("You: ").strip()
        if not transcription:
            continue
        
        if any(word in transcription.lower() for word in ["ava"]):
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
                add_message(role="user", content=user_msg, tool_id='')
                
                # Wait for the response to fully complete
                while main_running.is_set() or main_runner.is_set():
                    time.sleep(0.05)
                
                # Prompt for next input
                transcription = input("You: ").strip()
                if transcription:
                    if not stop_event.is_set():
                        stop_event.set()
                    continue
                break
            
            # Conversation ended — save it
            save_current_conversation()
            print(f"[{ASSISTANT_NAME.upper()}] Conversation saved. Say my name when you need me.")
            
        elif transcription.lower() == "print messages":
            from core.messageHandler import messages
            print(messages)
        elif transcription.lower() == "print memories":
            from knowledge.memory import retrieve_memories
            print(retrieve_memories())
        elif transcription.lower() == "print conversations":
            from knowledge.ConversationManager import get_manager
            mgr = get_manager()
            for conv_id, info in mgr.conversations_index.items():
                print(f"- {info.get('name', conv_id)}: {info.get('summary', 'No summary')}")


# ============================================================================
# CONFIGURATION: Choose wake mode here
# ============================================================================
WAKE_MODE = "continuous" 


def start():
    """
    Application entry point: starts the main response thread and
    activates the chosen wake mode (continuous, vosk, or text).
    """
    main_runner.clear()
    # Start the main response loop in a background daemon thread
    threading.Thread(target=main, daemon=True).start()
    
    print(f"[{ASSISTANT_NAME.upper()}] Starting in '{WAKE_MODE}' wake mode...")
    
    if WAKE_MODE == "vosk":
        wake_vosk()
    elif WAKE_MODE == "continuous":
        wake()
    elif WAKE_MODE == "text":
        wake_temp()
    else:
        print(f"[ERROR] Unknown wake mode: {WAKE_MODE}. Using 'continuous'.")
        wake()


if __name__ == "__main__":
    start()