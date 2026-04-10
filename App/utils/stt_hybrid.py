"""
stt_hybrid.py - Speech to Text with LIVE display (Vosk) + ACCURATE transcription (Whisper)

Best of both worlds:
- Shows live transcription as you speak (using Vosk locally)
- Final transcription uses Whisper via Groq (more accurate)

Same interface as stt.py for drop-in compatibility.

Performance optimizations applied:
- Persistent audio stream (kept alive between record() calls, avoids ~200ms open/close overhead)
- KaldiRecognizer reuse via Reset() instead of reconstruction
- deque-based pre-buffer for O(1) append/evict instead of O(n) list.pop(0)
- Merged duplicate voice detection methods into single _is_voice()
- Reduced default silence duration (2.0s → 1.5s) for faster response turnaround
"""

import os
import sys
import json
import time
import queue
import threading
import pyaudio
import numpy as np
import scipy.io.wavfile as wav
from collections import deque
from datetime import datetime
from typing import Optional, Tuple, List
from vosk import Model, KaldiRecognizer
from groq import Groq
import logging

from core.AppStates import stop_event
_settings_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'settings.json')
try:
    with open(_settings_path, 'r') as _f:
        os.environ.update({k: str(v) for k, v in json.load(_f).items()})
except Exception:
    pass
# Groq client for Whisper transcription
groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))

# Path to Vosk model (for live display only)
MODEL_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "vosk-model")

# Global Vosk model (loaded once at first use)
_vosk_model = None


def _load_vosk_model():
    """Load Vosk model for live display (singleton — only loaded once)."""
    global _vosk_model
    if _vosk_model is None:
        if not os.path.exists(MODEL_PATH):
            print(f"[STT] Warning: Vosk model not found at {MODEL_PATH}")
            print("[STT] Warning: Live display disabled.")
            return None
        print("[STT] Loading Vosk model...")
        _vosk_model = Model(MODEL_PATH)

    return _vosk_model


def transcribe_whisper(filename) -> str | None:
    """
    Transcribe audio file using Whisper (via Groq API).
    This is the accurate final transcription — called after Vosk live display.

    Args:
        filename: Path to audio file (absolute or relative to utils/)

    Returns:
        Transcribed text or None on error
    """
    # Resolve relative paths against utils/ directory
    if not os.path.isabs(filename):
        filename = os.path.join(os.path.dirname(__file__), filename)
    
    if not os.path.exists(filename):
        print(f"[STT] File not found: {filename}")
        return None
    
    try:
        with open(filename, "rb") as file:
            transcription = groq_client.audio.transcriptions.create(
                file=(filename, file.read()),
                model="whisper-large-v3-turbo",
                response_format="text",
            )
            return str(transcription)
    except Exception as e:
        logging.error(f"[Whisper] Error transcribing: {e}")
        return None


def transcribe_bytes(wav_bytes: bytes) -> str | None:
    """
    Transcribe raw WAV bytes using Whisper (via Groq API).
    Writes bytes to a temp file, then delegates to transcribe_whisper().
    Used by __main_server__.py when audio arrives over WebSocket.
    """
    import tempfile
    try:
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
            f.write(wav_bytes)
            temp_path = f.name
        result = transcribe_whisper(temp_path)
        os.unlink(temp_path)
        return result
    except Exception as e:
        logging.error(f"[STT] Error transcribing bytes: {e}")
        return None


class VoiceRecorder:
    """
    Voice recorder with:
    - Live Vosk display while speaking
    - Whisper transcription for final result (via background thread)
    - Persistent PyAudio stream (avoids ~200ms open/close overhead per call)
    - PyAudio callback queue to prevent frame dropping
    - Audio energy-based silence detection

    Usage:
        recorder = VoiceRecorder()
        success, filename = recorder.record(timeout=10)
        if success:
            text = recorder.get_whisper_result(timeout=15)
    """

    def __init__(self, sample_rate=16000, threshold=0.002, silence_duration=1.5, min_record_time=1.0, status_callback=None):
        self.sample_rate = sample_rate
        self.status_callback = status_callback
        self.chunk_size = 4000
        self.base_threshold = threshold
        self.threshold = threshold
        # Reduced from 2.0s → 1.5s for faster turnaround after user stops speaking
        self.silence_duration = silence_duration
        self.min_record_time = min_record_time
        
        # Adaptive noise floor calibration
        self.noise_floor = 0.005
        self.noise_samples = []
        self.noise_calibration_frames = 20       # Frames used for initial noise floor estimate
        self.is_calibrated = False
        self.last_calibration_time = 0
        self.recalibration_interval = 60         # Re-calibrate noise every 60s of silence

        # Consecutive voice frames required before recording starts (prevents noise triggers)
        self.voice_confirm_frames = 3
        self.consecutive_voice = 0
        
        # Vosk model for live display (loaded once, shared across instances)
        self.vosk_model = _load_vosk_model()
        self.recognizer = None
        if self.vosk_model:
            self.recognizer = KaldiRecognizer(self.vosk_model, sample_rate)
        
        # PyAudio — persistent stream to avoid repeated open/close overhead
        self.audio = pyaudio.PyAudio()
        self.stream = None
        self.audio_queue = queue.Queue()
        
        self.frames = []
        # Pre-buffer: ~1 second of audio kept before voice detection to capture word onsets.
        # Uses deque for O(1) append and eviction instead of list.pop(0) which is O(n).
        self.pre_buffer_size = int(1.0 * sample_rate / self.chunk_size)

        # Background Whisper state — transcription runs in a separate thread
        self._whisper_result = None
        self._whisper_done = threading.Event()
        
        # Perform initial noise floor calibration
        self._calibrate_at_startup()
    
    def _audio_callback(self, in_data, frame_count, time_info, status):
        """PyAudio stream callback — pushes raw audio chunks into a thread-safe queue."""
        if in_data:
            self.audio_queue.put(in_data)
        return (None, pyaudio.paContinue)

    def _ensure_stream(self):
        """
        Open the audio stream if it isn't already running.
        Keeps the stream alive across multiple record() calls to avoid
        the ~200ms overhead of opening/closing PyAudio streams each time.
        """
        if self.stream is not None and self.stream.is_active():
            return  # Stream already running

        # Close stale stream if it exists but is no longer active
        if self.stream is not None:
            try:
                self.stream.stop_stream()
                self.stream.close()
            except Exception:
                pass

        self.stream = self.audio.open(
            format=pyaudio.paInt16, channels=1, rate=self.sample_rate,
            input=True, frames_per_buffer=self.chunk_size,
            stream_callback=self._audio_callback
        )
        self.stream.start_stream()

    def close_stream(self):
        """Explicitly close the audio stream (call when completely done recording)."""
        if self.stream is not None:
            try:
                self.stream.stop_stream()
                self.stream.close()
            except Exception:
                pass
            self.stream = None

    def _calibrate_at_startup(self):
        """
        One-time noise floor calibration at startup.
        Opens a temporary stream, reads ambient noise for ~20 frames,
        and sets the voice detection threshold accordingly.
        """
        try:
            stream = self.audio.open(
                format=pyaudio.paInt16, channels=1, rate=self.sample_rate,
                input=True, frames_per_buffer=self.chunk_size
            )
            samples = []
            for _ in range(self.noise_calibration_frames):
                data = stream.read(self.chunk_size, exception_on_overflow=False)
                audio_array = np.frombuffer(data, dtype=np.int16)
                energy = np.mean(np.abs(audio_array)) / 32768.0
                samples.append(energy)
            stream.stop_stream()
            stream.close()
            
            if samples:
                self.noise_floor = np.mean(samples)
                # Threshold = (mean + std) * 4.0 — high multiplier for noise rejection
                self.threshold = max(self.base_threshold, (self.noise_floor + np.std(samples)) * 4.0)
                self.threshold = max(0.004, min(0.1, self.threshold))
            self.is_calibrated = True
            self.last_calibration_time = time.time()
        except Exception as e:
            print(f"[STT] Calibration error: {e}")
            self.is_calibrated = True
            
    def _calibrate_noise(self, audio_bytes: bytes) -> bool:
        """
        Incremental noise calibration from ambient audio frames.
        Called when is_calibrated is False. Returns True once enough
        frames have been collected to set the threshold.
        """
        audio_array = np.frombuffer(audio_bytes, dtype=np.int16)
        energy = np.mean(np.abs(audio_array)) / 32768.0
        self.noise_samples.append(energy)
        if len(self.noise_samples) >= self.noise_calibration_frames:
            self.noise_floor = np.mean(self.noise_samples)
            self.threshold = max(self.base_threshold, (self.noise_floor + np.std(self.noise_samples)) * 4.0)
            self.threshold = max(0.004, min(0.1, self.threshold))
            self.is_calibrated = True
            self.last_calibration_time = time.time()
            return True
        return False
        
    def _update_noise_floor(self, audio_bytes: bytes):
        """
        Slowly adapt the noise floor during silence periods using exponential
        moving average. Handles gradual environment changes (e.g., fan turning on).
        Only updates once per recalibration_interval seconds.
        """
        current_time = time.time()
        if current_time - self.last_calibration_time > self.recalibration_interval:
            audio_array = np.frombuffer(audio_bytes, dtype=np.int16)
            energy = np.mean(np.abs(audio_array)) / 32768.0
            if energy < self.threshold * 0.5:
                self.noise_floor = self.noise_floor * 0.9 + energy * 0.1
                self.threshold = max(self.base_threshold, self.noise_floor * 4.0)
                self.threshold = max(0.004, min(0.1, self.threshold))
                self.last_calibration_time = current_time

    def _is_voice(self, audio_bytes: bytes) -> bool:
        """
        Check if an audio frame contains voice activity.
        Compares normalized energy (0.0–1.0) against the adaptive threshold.
        Used for both pre-buffer filtering and recording silence detection.
        (Previously split into _is_voice + _frame_has_voice — merged since identical.)
        """
        audio_array = np.frombuffer(audio_bytes, dtype=np.int16)
        energy = np.mean(np.abs(audio_array)) / 32768.0
        return energy > self.threshold

    def save_recording(self, filename: Optional[str] = None) -> str:
        """Save buffered audio frames to a WAV file in data/recordings/."""
        if not self.frames: raise ValueError("No audio data to save!")
        if not filename: filename = f"recording_{datetime.now().strftime('%Y%m%d_%H%M%S')}.wav"
        if not filename.endswith('.wav'): filename += '.wav'
        save_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "recordings")
        os.makedirs(save_dir, exist_ok=True)
        fullpath = os.path.join(save_dir, filename)
        wav.write(fullpath, self.sample_rate, np.frombuffer(b''.join(self.frames), dtype=np.int16))
        return fullpath

    def get_whisper_result(self, timeout: Optional[float] = None) -> Optional[str]:
        """
        Fetch the Whisper transcription that was started in the background after record().
        Blocks until the result is ready (or until timeout expires).
        Call this after record() returns — by the time your caller does setup,
        Whisper is likely already done or close to done.
        """
        self._whisper_done.wait(timeout=timeout)
        return self._whisper_result

    def record(self, timeout: Optional[float] = None, silence_duration: Optional[float] = None) -> Tuple[bool, Optional[str]]:
        """
        Record audio until voice is detected, then until silence.

        Flow:
        1. Wait for voice activity (energy > threshold for voice_confirm_frames consecutive frames)
        2. Record while voice is active, showing live Vosk transcription
        3. Stop after silence_duration seconds of silence
        4. Save WAV and kick off background Whisper transcription
        5. Return (True, filename) — call get_whisper_result() to get the text

        Args:
            timeout: Max seconds to wait for voice before giving up (None = forever)
            silence_duration: Override the default silence duration for this call

        Returns:
            (success: bool, filename: Optional[str])
        """
        self.frames = []
        self.consecutive_voice = 0

        # Drain any stale audio from the queue (accumulated between calls)
        while not self.audio_queue.empty():
            try: self.audio_queue.get_nowait()
            except queue.Empty: break
            
        # Reset Vosk recognizer for a clean slate (much faster than constructing a new one)
        if self.recognizer:
            self.recognizer.Reset()
            
        start_time = time.time()
        recording = False
        silent_chunks = 0
        recorded_chunks = 0

        # Allow per-call silence_duration override (e.g., longer for continued conversations)
        effective_silence = silence_duration if silence_duration is not None else self.silence_duration
        
        # Convert time-based thresholds to chunk counts
        max_silent_chunks = int(effective_silence * self.sample_rate / self.chunk_size)
        min_recorded_chunks = int(self.min_record_time * self.sample_rate / self.chunk_size)
        
        last_partial = ""
        # Pre-buffer: deque with fixed max size — automatically evicts oldest frames on append
        pre_buffer = deque(maxlen=self.pre_buffer_size)
        # Accumulates frames during the voice confirmation window
        confirm_buffer = []
        
        try:
            # Reuse existing stream or open a new one (persistent stream optimization)
            self._ensure_stream()
            
            while True:
                # Timeout handling
                if timeout is not None:
                    if not recording and (time.time() - start_time > timeout): return False, None
                    if recording and (time.time() - start_time > 60):
                        # Safety cap: don't record forever
                        print()
                        return True, self.save_recording()
                
                # Get next audio chunk from callback queue
                try: data = self.audio_queue.get(timeout=0.1)
                except queue.Empty: continue
                
                # Run noise calibration if needed
                if not self.is_calibrated:
                    self._calibrate_noise(data)
                    continue
                
                is_voice_now = self._is_voice(data)
                
                # ── PRE-RECORDING: waiting for confirmed voice activity ──
                if not recording:
                    if is_voice_now:
                        # Show [Listening] immediately on first voice frame for user feedback
                        if self.consecutive_voice == 0:
                            print("\r[Listening] ", end="", flush=True)
                            if self.status_callback: self.status_callback("[Listening] Speech detected...")

                        self.consecutive_voice += 1
                        confirm_buffer.append(data)
                        pre_buffer.append(data)

                        # Voice confirmed after N consecutive frames — start recording
                        if self.consecutive_voice >= self.voice_confirm_frames:
                            recording = True
                            recorded_chunks = 0
                            silent_chunks = 0
                            self.frames = list(pre_buffer)

                            # Signal TTS to stop (barge-in: user is speaking)
                            if not stop_event.is_set():
                                stop_event.set()

                            # Feed only voice-active pre-buffer frames to Vosk for live display
                            if self.recognizer:
                                for pre_data in pre_buffer:
                                    if self._is_voice(pre_data):
                                        self.recognizer.AcceptWaveform(pre_data)

                            confirm_buffer = []
                    else:
                        # Silent frame — reset voice confirmation counter
                        if self.consecutive_voice > 0:
                            # Clear the [Listening] indicator if it was a false trigger
                            print("\r                    \r", end="", flush=True)
                            if self.status_callback: self.status_callback("Waiting for voice...")
                        self.consecutive_voice = 0
                        confirm_buffer = []
                        self._update_noise_floor(data)
                        pre_buffer.append(data)
                    continue
                
                # ── RECORDING: capturing audio ──
                self.frames.append(data)
                recorded_chunks += 1
                
                # Feed to Vosk for live display (only while voice is active, not during silence tail)
                if self.recognizer and silent_chunks == 0:
                    if self.recognizer.AcceptWaveform(data):
                        result = json.loads(self.recognizer.Result())
                        if text := result.get("text", ""):
                            print(f"\r>> {text}                    ", end="", flush=True)
                            if self.status_callback: self.status_callback(f"Hearing: {text}")
                            last_partial = ""
                    else:
                        partial_text = json.loads(self.recognizer.PartialResult()).get("partial", "")
                        if partial_text and partial_text != last_partial:
                            last_partial = partial_text
                            print(f"\r>> {partial_text}          ", end="", flush=True)
                            if partial_text and self.status_callback:
                                self.status_callback(f"Hearing: {partial_text}")
                
                # ── SILENCE DETECTION ──
                if not is_voice_now:
                    silent_chunks += 1
                    if silent_chunks >= max_silent_chunks:
                        if recorded_chunks >= min_recorded_chunks:
                            # Recording complete — save and start background Whisper
                            print()
                            if self.status_callback: self.status_callback("Transcribing with Whisper...")
                            filename = self.save_recording()
                            # Fire Whisper in background thread immediately
                            self._whisper_result = None
                            self._whisper_done = threading.Event()
                            def _run_whisper(fn):
                                self._whisper_result = transcribe_whisper(fn)
                                self._whisper_done.set()
                            threading.Thread(target=_run_whisper, args=(filename,), daemon=True).start()
                            return True, filename
                        else:
                            # Recording too short — discard and go back to listening
                            recording = False
                            silent_chunks = 0
                            self.consecutive_voice = 0
                            # Reset Vosk state to prevent bleed into next attempt
                            if self.recognizer:
                                self.recognizer.Reset()
                            pre_buffer = deque(
                                self.frames[-self.pre_buffer_size:] if len(self.frames) > self.pre_buffer_size else self.frames,
                                maxlen=self.pre_buffer_size
                            )
                            self.frames = []
                            print("\r[Listening] ", end="", flush=True)
                            if self.status_callback: self.status_callback("[Listening] Speech detected...")
                else:
                    silent_chunks = 0
                    
        except KeyboardInterrupt:
            print("\n[Interrupted]")
            if recording and self.frames: return True, self.save_recording()
            return False, None
        except Exception as e:
            print(f"\n[STT] Error: {e}")
            return False, None
        # NOTE: No finally block closing the stream — stream is kept alive
        # for reuse on the next record() call (persistent stream optimization).
        # Stream is cleaned up in close_stream() or __del__().

    def __del__(self):
        """Cleanup: close stream and terminate PyAudio on garbage collection."""
        try:
            self.close_stream()
            self.audio.terminate()
        except: pass

# For testing
if __name__ == "__main__":
    print("=" * 50)
    print("Testing Hybrid STT (Vosk live + Whisper final)")
    print("=" * 50)
    print("\nSpeak something... (stops after 1.5s silence)")
    
    recorder = VoiceRecorder()
    success, filename = recorder.record(timeout=30)
    
    if success:
        print(f"\nSaved to: {filename}")
        print("[Waiting for Whisper...]")
        text = recorder.get_whisper_result(timeout=15)
        print(f"\n[Final Whisper Result]: {text}")
    else:
        print("No recording made")
    recorder.close_stream()