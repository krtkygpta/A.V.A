import threading

lock = threading.Lock()
stop_event = threading.Event()
wake_event = threading.Event()
speak_thread = threading.Event()
main_runner = threading.Event()
voice_stop_event = threading.Event()
wakeword_stop_event = threading.Event()
stop_chat_flag = threading.Event()

# Conversation state tracking for preventing premature ends
expecting_followup = threading.Event()  # Model has indicated more responses coming
conversation_extended = threading.Event()  # Conversation timeout has been extended
work_in_progress = threading.Event()  # Model is currently working on something
background_task_active = threading.Event()  # Background task is running, keep conversation open
# True while continuous/wakeword voice mode is running (even between chats).
# Lets DONE announces still wake the model — text mode leaves this clear.
voice_session_active = threading.Event()


def reset_conversation_flags():
    """Reset all conversation-related flags. Call when conversation ends."""
    expecting_followup.clear()
    conversation_extended.clear()
    work_in_progress.clear()
    # Note: background_task_active is cleared separately when tasks complete
    # Note: voice_session_active stays set for the whole voice-mode lifetime
