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


def synthesize_bytes(text: str) -> bytes | None:
    """
    Request WAV bytes from server-side TTS.
    Returns None on error.
    """
    try:
        return synthesize_remote_tts(text)
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


def speak(text: str, stop_event: threading.Event) -> None:
    """
    Synthesise text on server and play it locally.
    """
    global _last_duration

    _duration_ready.clear()

    if stop_event.is_set():
        _duration_ready.set()
        return

    wav_bytes = synthesize_bytes(text)
    if not wav_bytes:
        _duration_ready.set()
        return

    if stop_event.is_set():
        _duration_ready.set()
        return

    # Fast duration estimate for conversation timeout logic.
    _last_duration = _wav_duration(wav_bytes)

    buf = io.BytesIO(wav_bytes)
    data, samplerate = sf.read(buf, dtype="float32")

    pad_shape = (int(samplerate * TAIL_SILENCE_S),) if data.ndim == 1 else (int(samplerate * TAIL_SILENCE_S), data.shape[1])
    data = np.concatenate([data, np.zeros(pad_shape, dtype="float32")])

    if _last_duration <= 0.0:
        _last_duration = len(data) / float(samplerate or 1)

    _duration_ready.set()

    sd.play(data, samplerate)
    while sd.get_stream().active:
        if stop_event.is_set():
            sd.stop()
            return
        time.sleep(0.1)
    sd.wait()


def shutdown_tts() -> None:
    """
    No-op on client: server owns the TTS engine lifecycle.
    """
    return None
