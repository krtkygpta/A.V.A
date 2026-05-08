from app import run_server

if __name__ == "__main__":
    import threading

    from memorySystem import memoryDreamer

    t1 = threading.Thread(target=memoryDreamer.DreamerStart, daemon=True)
    t1.start()
    print("starting server")
    run_server()
