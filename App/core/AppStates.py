import threading

lock = threading.Lock()
stop_event = threading.Event()
wake_event = threading.Event()
speak_thread = threading.Event()
main_runner = threading.Event()