import winsound
import threading
import time
import json


def ring_timer(duration_seconds: int, frequency: int = 1000, beep_interval: float = 0.5) -> bool:
    """
    Rings a beeping sound for a given duration.
    Stops and returns True if any keyboard input is detected.
    Returns False if the timer expires without input.

    Args:
        duration_seconds: How long to ring (in seconds)
        frequency: Beep frequency in Hz (default 1000)
        beep_interval: Time between beeps in seconds (default 0.5)

    Returns:
        True if user pressed a key, False if timer expired
    """
    import msvcrt

    stop_event = threading.Event()
    user_pressed = threading.Event()

    def beep_loop():
        end_time = time.time() + duration_seconds
        while not stop_event.is_set() and time.time() < end_time:
            winsound.Beep(frequency, int(beep_interval * 1000 // 2))
            time.sleep(beep_interval / 2)
        stop_event.set()

    def input_listener():
        while not stop_event.is_set():
            if msvcrt.kbhit():
                msvcrt.getch()  # consume the key press
                user_pressed.set()
                stop_event.set()
                return
            time.sleep(0.05)

    beep_thread = threading.Thread(target=beep_loop, daemon=True)
    input_thread = threading.Thread(target=input_listener, daemon=True)

    beep_thread.start()
    input_thread.start()

    beep_thread.join()
    stop_event.set()
    input_thread.join(timeout=0.2)

    return json.dumps({'status': 'success', 'content': 'Timer expired,try again'}) if not user_pressed.is_set() else json.dumps({'status': 'success', 'content': 'User responded'})


# --- Example usage ---
if __name__ == "__main__":
    print("Timer starting! Press any key to stop it early...")
    result = ring_timer(duration_seconds=10, frequency=750, beep_interval=0.35)

    if result:
        print("✅ You stopped the timer early!")
    else:
        print("⏰ Timer expired with no input.")