import io
import threading
import time
import wave

import numpy as np
import sounddevice as sd
import soundfile as sf

from core.server_api import SERVER_URL, synthesize_remote_tts


TAIL_SILENCE_S = 0.3

_initialized = False
_last_duration: float = 0.0
_duration_ready = threading.Event()

# Playback session tracking so the response loop can wait for speech to finish
# before kicking the next announce turn (avoids stop_event cutting mid-sentence).
_speech_lock = threading.Lock()
_speech_gen = 0
_playing = False
_play_done = threading.Event()
_play_done.set()  # idle at startup


def init_tts() -> bool:
    """
    Client-side init is now lightweight because TTS runs on the server.
    """
    global _initialized
    _initialized = True
    return True


def get_last_duration(timeout: float = 15.0) -> float:
    """
    Block until speak() has calculated audio duration, then return it.
    """
    if _duration_ready.wait(timeout=timeout):
        return _last_duration
    return 0.0


def is_playing() -> bool:
    """True while a speak() session is synthesizing or playing audio."""
    with _speech_lock:
        return _playing


def wait_until_done(timeout: float | None = None) -> bool:
    """
    Wait until the current speak session finishes (or is interrupted).
    Returns True if idle, False on timeout.
    """
    return _play_done.wait(timeout=timeout)


def note_speech_started() -> int:
    """
    Call on the main/UI thread *before* starting the speak worker thread.
    Clears the done event so waiters block even before the worker runs.
    Returns a generation id used to ignore stale finally blocks.
    """
    global _speech_gen, _playing
    with _speech_lock:
        _speech_gen += 1
        gen = _speech_gen
        _playing = True
        _play_done.clear()
        return gen


def _finish_speech(gen: int) -> None:
    global _playing
    with _speech_lock:
        # Only the latest session may mark idle (older interrupted sessions ignore).
        if gen != _speech_gen:
            return
        _playing = False
        _play_done.set()


def synthesize_bytes(text: str) -> bytes | None:
    """
    Request WAV bytes from server-side TTS.
    Returns None on error.
    """
    try:
        import re
        # Clean any stray markdown formatting so Piper TTS never stumbles on asterisks or symbols
        cleaned = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
        cleaned = re.sub(r"\*([^*]+)\*", r"\1", cleaned)
        cleaned = re.sub(r"__([^_]+)__", r"\1", cleaned)
        cleaned = re.sub(r"`([^`]+)`", r"\1", cleaned)
        return synthesize_remote_tts(cleaned.strip())
    except Exception as exc:
        print(f"[TTS] Remote synthesis error ({SERVER_URL}): {exc}")
        return None


def _wav_duration(wav_bytes: bytes) -> float:
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wav_file:
            rate = wav_file.getframerate() or 1
            frames = wav_file.getnframes()
            return frames / float(rate)
    except Exception:
        return 0.0


def speak(text: str, stop_event: threading.Event, gen: int | None = None) -> None:
    """
    Synthesise text on server and play it locally.

    If *gen* is None, a new speech session is opened here (CLI/TUI should
    prefer note_speech_started() before spawning the thread for race-free waits).
    """
    global _last_duration

    if gen is None:
        gen = note_speech_started()

    _duration_ready.clear()

    try:
        if stop_event.is_set():
            _last_duration = 0.0
            _duration_ready.set()
            return

        wav_bytes = synthesize_bytes(text)
        if not wav_bytes:
            _last_duration = 0.0
            _duration_ready.set()
            return

        if stop_event.is_set():
            _last_duration = 0.0
            _duration_ready.set()
            return

        # Fast duration estimate for conversation timeout logic.
        _last_duration = _wav_duration(wav_bytes)

        buf = io.BytesIO(wav_bytes)
        data, samplerate = sf.read(buf, dtype="float32")

        pad_shape = (
            (int(samplerate * TAIL_SILENCE_S),)
            if data.ndim == 1
            else (int(samplerate * TAIL_SILENCE_S), data.shape[1])
        )
        data = np.concatenate([data, np.zeros(pad_shape, dtype="float32")])

        if _last_duration <= 0.0:
            _last_duration = len(data) / float(samplerate or 1)

        _duration_ready.set()

        if stop_event.is_set():
            return

        sd.play(data, samplerate)
        while True:
            try:
                stream = sd.get_stream()
                if stream is None or not stream.active:
                    break
            except Exception:
                break
            if stop_event.is_set():
                try:
                    sd.stop()
                except Exception:
                    pass
                return
            time.sleep(0.05)
        try:
            sd.wait()
        except Exception:
            pass
    finally:
        # Ensure duration waiters are never stuck if we bailed early.
        if not _duration_ready.is_set():
            _duration_ready.set()
        _finish_speech(gen)


def shutdown_tts() -> None:
    """
    No-op on client: server owns the TTS engine lifecycle.
    """
    return None
