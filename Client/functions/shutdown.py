
import os
import threading
import time
import json
import keyboard  # This library is used for detecting keypresses

def system_action(action, delay_seconds=10):
    """
    action: either 'shutdown' or 'restart'
    delay_seconds: time in seconds after which to perform the action
    """
    cancel_event = threading.Event()

    def shutdown_or_restart():
        for _ in range(delay_seconds):
            time.sleep(1)
            if cancel_event.is_set():
                print("Shutdown/Restart has been canceled due to key press.")
                return
        
        if action == 'shutdown':
            os.system("shutdown /s /t 0")
        elif action == 'restart':
            os.system("shutdown /r /t 0")
        else:
            raise ValueError("Action must be either 'shutdown' or 'restart'")
    
    def detect_keypress():
        # Detects any key press and sets the cancel_event
        keyboard.read_event()
        cancel_event.set()
    
    # Start the shutdown/restart thread
    thread = threading.Thread(target=shutdown_or_restart)
    thread.start()

    # Start the keypress detection thread
    keypress_thread = threading.Thread(target=detect_keypress)
    keypress_thread.start()
    # Return a confirmation message immediately
    return json.dumps({
        'status': 'success', 'content': f'Initiating {action} in {delay_seconds} seconds'})