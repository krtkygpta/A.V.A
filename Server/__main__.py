import sys
from pathlib import Path

_SERVER_DIR = str(Path(__file__).resolve().parent)
if _SERVER_DIR not in sys.path:
    sys.path.insert(0, _SERVER_DIR)

from app import run_server

if __name__ == "__main__":
    import threading

    from memorySystem import memoryDreamer

    t1 = threading.Thread(target=memoryDreamer.DreamerStart, daemon=True)
    t1.start()
    print("starting server")
    run_server()
