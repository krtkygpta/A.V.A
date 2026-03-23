"""
piper_tts.py  —  AVA TTS module

Usage:
    import threading
    from piper_tts import init_tts, speak, shutdown_tts

    init_tts()                          # call once at startup

    stop_event = threading.Event()
    t = threading.Thread(target=speak, args=("Hello from AVA", stop_event))
    t.start()

    # to interrupt at any time:
    stop_event.set()

    shutdown_tts()                      # call on app exit

Requirements:
    pip install "piper-tts[http]" requests sounddevice soundfile numpy
"""

import io
import subprocess
import sys
import threading
import time

import numpy as np
import requests
import sounddevice as sd
import soundfile as sf

# ── Config ────────────────────────────────────────────────────────────────────
VOICE           = "en_US-hfc_female-medium"
SERVER_HOST     = "127.0.0.1"
SERVER_PORT     = 5000
BASE_URL        = f"http://{SERVER_HOST}:{SERVER_PORT}"
STARTUP_TIMEOUT = 60
TAIL_SILENCE_S  = 0.3   # seconds of silence padded to prevent tail cutoff
# ─────────────────────────────────────────────────────────────────────────────

_server_proc: subprocess.Popen | None = None


# ── Internal helpers ──────────────────────────────────────────────────────────

def _download_voice() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "piper.download_voices", VOICE],
        capture_output=True,
    )
    if result.returncode != 0:
        print(f"[TTS] Warning: voice download exited {result.returncode} — continuing.")


def _start_server() -> subprocess.Popen:
    proc = subprocess.Popen(
        [sys.executable, "-m", "piper.http_server", "-m", VOICE,
         "--host", SERVER_HOST, "--port", str(SERVER_PORT)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    return proc


def _wait_for_server() -> bool:
    deadline = time.time() + STARTUP_TIMEOUT
    while time.time() < deadline:
        try:
            r = requests.post(f"{BASE_URL}/", json={"text": "hi"}, timeout=15)
            if r.status_code in (200, 400):
                return True
        except requests.exceptions.ConnectionError:
            pass
        time.sleep(0.5)
    return False


# ── Public API ────────────────────────────────────────────────────────────────

def init_tts() -> bool:
    """
    Download voice (if needed) and start the Piper HTTP server.
    Call once at application startup. Returns True on success.
    """
    global _server_proc
    _download_voice()
    _server_proc = _start_server()
    if not _wait_for_server():
        print("[TTS] Server failed to start.")
        if _server_proc:
            _server_proc.terminate()
        return False
    return True


# Shared duration — set by speak() after synthesis, read by get_last_duration()
_last_duration: float = 0.0
_duration_ready = threading.Event()


def get_last_duration(timeout: float = 15.0) -> float:
    """
    Block until speak() has calculated the audio duration, then return it.
    Returns 0.0 on timeout. Drop-in replacement for get_duration_wave().
    """
    if _duration_ready.wait(timeout=timeout):
        return _last_duration
    return 0.0


def speak(text: str, stop_event: threading.Event) -> None:
    """
    Synthesise `text` and play it.
    Designed to run in a thread — checks `stop_event` before and during playback.
    After synthesis, sets _duration_ready so get_last_duration() can return.

    Args:
        text:       Text to synthesise.
        stop_event: Set this Event to interrupt synthesis or playback.
    """
    global _last_duration
    _duration_ready.clear()

    if stop_event.is_set():
        _duration_ready.set()
        return

    # ── Synthesise ────────────────────────────────────────────────────────────
    try:
        response = requests.post(
            f"{BASE_URL}/",
            json={"text": text},
            timeout=30,
            stream=True,        # stream so stop_event can abort mid-download
        )
        response.raise_for_status()

        chunks = []
        for chunk in response.iter_content(chunk_size=4096):
            if stop_event.is_set():
                _duration_ready.set()
                return
            chunks.append(chunk)

        wav_bytes = b"".join(chunks)

    except requests.RequestException as e:
        print(f"[TTS] Synthesis error: {e}")
        _duration_ready.set()
        return

    if stop_event.is_set():
        _duration_ready.set()
        return

    # ── Decode WAV ────────────────────────────────────────────────────────────
    buf = io.BytesIO(wav_bytes)
    data, samplerate = sf.read(buf, dtype="float32")

    # Pad tail to prevent cutoff
    pad_shape = (int(samplerate * TAIL_SILENCE_S),) if data.ndim == 1 \
                else (int(samplerate * TAIL_SILENCE_S), data.shape[1])
    data = np.concatenate([data, np.zeros(pad_shape, dtype="float32")])

    # Expose duration before playback starts
    _last_duration = len(data) / samplerate
    _duration_ready.set()

    # ── Play audio, polling stop_event every 100ms ───────────────────────────
    # print(f"[TTS] Playing {_last_duration:.1f}s of audio ...")
    sd.play(data, samplerate)
    while sd.get_stream().active:
        if stop_event.is_set():
            sd.stop()
            return
        time.sleep(0.1)
    sd.wait()


def shutdown_tts() -> None:
    """Terminate the Piper server. Call on application exit."""
    global _server_proc
    if _server_proc and _server_proc.poll() is None:
        print("[TTS] Shutting down Piper server ...")
        _server_proc.terminate()
        _server_proc.wait()
        _server_proc = None


# ── Quick test ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if not init_tts():
        sys.exit(1)

    stop = threading.Event()
    try:
        while True:
            text = input("Text > ").strip()
            if not text:
                break
            stop.clear()
            t = threading.Thread(target=speak, args=(text, stop), daemon=True)
            t.start()
            t.join()
    except KeyboardInterrupt:
        stop.set()
    finally:
        shutdown_tts()