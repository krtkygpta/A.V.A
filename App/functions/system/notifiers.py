import ctypes
import json
import threading
import time
try:
    from plyer import notification
except ImportError:
    notification = None

# Register an explicit App User Model ID so Windows toasts are attributed to
# AVA in the Action Center and survive focus assist more reliably. This must
# happen before the first toast fires.
try:
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
        "AVA.AI.Assistant"
    )
except Exception:
    pass

# Active ping can be cancelled when a new one starts (or stop_ping()).
_ping_lock = threading.Lock()
_ping_stop_event: threading.Event | None = None
# Master cancel for continuous / multi-chunk pings (user spoke or stop_ping).
_ping_master_stop = threading.Event()
_ping_master_stop.set()  # idle = not ringing


def stop_ping() -> None:
    """Stop any in-flight audible ping (one-shot or continuous)."""
    global _ping_stop_event
    _ping_master_stop.set()
    with _ping_lock:
        if _ping_stop_event is not None:
            _ping_stop_event.set()
            _ping_stop_event = None


def is_pinging() -> bool:
    """True while a continuous/one-shot ping session is active."""
    return not _ping_master_stop.is_set()


def ring_timer(
    duration_seconds: int, frequency: int = 1000, beep_interval: float = 0.5
):
    """
    Rings a beeping sound for a given duration (blocking).
    Stops early if any keyboard input is detected or stop_ping() is called.

    Prefer ``ping()`` from the main-agent tool path — it is non-blocking so the
    response loop can keep speaking after the alert starts.
    """
    try:
        duration_seconds = max(1, min(int(duration_seconds), 120))
    except (TypeError, ValueError):
        duration_seconds = 5

    import msvcrt

    global _ping_stop_event
    stop_event = threading.Event()
    user_pressed = threading.Event()

    with _ping_lock:
        if _ping_stop_event is not None:
            _ping_stop_event.set()
        _ping_stop_event = stop_event

    def beep_loop():
        end_time = time.time() + duration_seconds
        while (
            not stop_event.is_set()
            and not _ping_master_stop.is_set()
            and time.time() < end_time
        ):
            try:
                winsound.Beep(frequency, max(50, int(beep_interval * 1000 // 2)))
            except Exception:
                break
            time.sleep(beep_interval / 2)
        stop_event.set()

    def input_listener():
        while not stop_event.is_set() and not _ping_master_stop.is_set():
            try:
                if msvcrt.kbhit():
                    msvcrt.getch()  # consume the key press
                    user_pressed.set()
                    stop_event.set()
                    _ping_master_stop.set()
                    return
            except Exception:
                return
            time.sleep(0.05)

    beep_thread = threading.Thread(target=beep_loop, daemon=True)
    input_thread = threading.Thread(target=input_listener, daemon=True)

    beep_thread.start()
    input_thread.start()

    beep_thread.join()
    stop_event.set()
    input_thread.join(timeout=0.2)

    with _ping_lock:
        if _ping_stop_event is stop_event:
            _ping_stop_event = None

    return (
        json.dumps({"status": "success", "content": "Timer expired,try again"})
        if not user_pressed.is_set()
        else json.dumps({"status": "success", "content": "User responded"})
    )


def _as_bool(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    s = str(value).strip().lower()
    if s in ("1", "true", "yes", "y", "on", "continuous"):
        return True
    if s in ("0", "false", "no", "n", "off"):
        return False
    return default


def ping(
    duration_seconds: int = 5,
    frequency: int = 1000,
    beep_interval: float = 0.5,
    continuous: bool = False,
    max_seconds: int = 180,
):
    """
    Non-blocking audible alert for the main agent tool ``ping``.

    Starts ringing on a daemon thread and returns immediately so AVA can still
    speak. Any previous ping is cancelled. Press any key, speak to AVA, or call
    stop_ping to silence.

    continuous=True → keep ringing in chunks until the user responds (or max_seconds).
    Use for real alarms. Casual reminders should use send_notification only, or a
    short duration_seconds ping (2–4s).
    """
    if _as_bool(continuous, False):
        return continuous_ping(
            max_seconds=max_seconds if max_seconds is not None else 180,
            chunk_seconds=12,
            frequency=frequency,
            beep_interval=beep_interval,
        )

    try:
        duration_seconds = max(1, min(int(duration_seconds), 120))
    except (TypeError, ValueError):
        duration_seconds = 5

    # Cancel prior session, then start a new one-shot.
    stop_ping()
    _ping_master_stop.clear()

    def _run():
        try:
            ring_timer(
                duration_seconds=duration_seconds,
                frequency=frequency,
                beep_interval=beep_interval,
            )
        except Exception as e:
            print(f"[Notifier] ping failed: {e}")
        finally:
            _ping_master_stop.set()

    threading.Thread(target=_run, daemon=True, name="ava-ping").start()
    return json.dumps(
        {
            "status": "success",
            "content": f"Ping started for {duration_seconds}s (non-blocking)",
        }
    )


def continuous_ping(
    max_seconds: int = 180,
    chunk_seconds: int = 12,
    frequency: int = 1000,
    beep_interval: float = 0.5,
):
    """
    Keep ringing in chunks until stop_ping() / user key / max_seconds.

    Used for timers ("ring until I respond"). Non-blocking; returns immediately.
    """
    try:
        max_seconds = max(5, min(int(max_seconds), 600))
    except (TypeError, ValueError):
        max_seconds = 180
    try:
        chunk_seconds = max(3, min(int(chunk_seconds), 30))
    except (TypeError, ValueError):
        chunk_seconds = 12

    stop_ping()
    _ping_master_stop.clear()

    def _run():
        end = time.time() + max_seconds
        try:
            while time.time() < end and not _ping_master_stop.is_set():
                remaining = end - time.time()
                if remaining <= 0:
                    break
                ring_timer(
                    duration_seconds=min(chunk_seconds, int(remaining) or 1),
                    frequency=frequency,
                    beep_interval=beep_interval,
                )
                # Brief gap between chunks so it is not one continuous wall of sound
                if not _ping_master_stop.is_set():
                    time.sleep(0.35)
        except Exception as e:
            print(f"[Notifier] continuous_ping failed: {e}")
        finally:
            _ping_master_stop.set()

    threading.Thread(target=_run, daemon=True, name="ava-continuous-ping").start()
    return json.dumps(
        {
            "status": "success",
            "content": (
                f"Continuous ping started (max {max_seconds}s, "
                f"chunk {chunk_seconds}s); stops when user responds"
            ),
        }
    )


def _show_toast_blocking(
    title: str, message: str, timeout: int = 10, *, sound: bool = False
) -> None:
    """Show a desktop toast; optional MessageBeep (only when sound=True)."""
    if sound:
        try:
            winsound.MessageBeep(0x00000040)  # MB_ICONASTERISK
        except Exception:
            pass
    if notification is not None:
        try:
            notification.notify(
                title=str(title)[:80],
                message=str(message)[:200],
                app_name="AVA",
                timeout=timeout,
            )
        except Exception as e:
            print(f"[Notifier] {e}")


def show_toast_only(title, message, timeout: int = 10):
    """Desktop toast with no beep — for silent system completions."""
    threading.Thread(
        target=_show_toast_blocking,
        args=(str(title)[:80], str(message)[:200], timeout),
        kwargs={"sound": False},
        daemon=True,
    ).start()
    try:
        from core.gateway import broadcast_notification
        broadcast_notification(
            title=str(title),
            message=str(message),
            priority="normal",
            sound=False
        )
    except Exception:
        pass
    return json.dumps(
        {"status": "success", "content": "Toast shown (no sound)"}
    )


def send_notification(title, message):
    """Show a desktop toast + chime — intentional user/agent notify tool.

    Background job completion must NOT call this by default; use show_toast_only
    or stay silent so the main agent can decide.
    """
    threading.Thread(
        target=_show_toast_blocking,
        args=(str(title)[:80], str(message)[:200], 10),
        kwargs={"sound": True},
        daemon=True,
    ).start()
    try:
        from core.gateway import broadcast_notification
        broadcast_notification(
            title=str(title),
            message=str(message),
            priority="high",
            sound=True
        )
    except Exception:
        pass
    return json.dumps(
        {
            "status": "success",
            "content": f"Notification sent: {str(title)[:60]}",
        }
    )


# Example usage
# --- Example usage ---
if __name__ == "__main__":
    print("Timer starting! Press any key to stop it early...")
    result = ring_timer(duration_seconds=10, frequency=750, beep_interval=0.35)

    if result:
        print("✅ You stopped the timer early!")
    else:
        print("⏰ Timer expired with no input.")
