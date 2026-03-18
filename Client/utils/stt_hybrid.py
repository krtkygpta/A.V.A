"""
stt_hybrid.py - Speech to Text with LIVE display (Vosk) + ACCURATE transcription (Whisper)

Best of both worlds:
- Shows live transcription as you speak (using Vosk locally)
- Final transcription uses Whisper via Groq (more accurate)

Same interface as stt.py for drop-in compatibility.
"""

import os
import sys
import json
import time
import queue
import pyaudio
import numpy as np
import scipy.io.wavfile as wav
from datetime import datetime
from typing import Optional, Tuple, List
from vosk import Model, KaldiRecognizer
from groq import Groq
import logging

from core.AppStates import stop_event
import dotenv

dotenv.load_dotenv()
# Groq client for Whisper transcription
groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))

# Path to Vosk model (for live display only)
MODEL_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "vosk-model")

# Global Vosk model (loaded once)
_vosk_model = None


def _load_vosk_model():
    """Load Vosk model for live display (singleton)."""
    global _vosk_model
    if _vosk_model is None:
        if not os.path.exists(MODEL_PATH):
            print(f"[Warning] Vosk model not found at {MODEL_PATH}")
            print("[Warning] Live transcription display disabled.")
            return None
        print("[Vosk] Loading model for live display...")
        _vosk_model = Model(MODEL_PATH)
        print("[Vosk] Ready.")
    return _vosk_model


def transcribe_whisper(filename) -> str | None:
    """
    Transcribe audio file using Whisper (via Groq API).
    This is the accurate final transcription.
    
    Args:
        filename: Path to audio file
    
    Returns:
        Transcribed text or None on error
    """
    # Handle relative paths
    if not os.path.isabs(filename):
        filename = os.path.join(os.path.dirname(__file__), filename)
    
    if not os.path.exists(filename):
        print(f"[Whisper] File not found: {filename}")
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


class VoiceRecorder:
    """
    Voice recorder with:
    - Live Vosk display while speaking
    - Whisper transcription for final result
    - PyAudio Queue to prevent frame dropping (no cuts in between)
    - Audio frame-based silence detection (no end cutoffs)
    """
    def __init__(self, sample_rate=16000, threshold=0.002, silence_duration=2.0, min_record_time=1.0):
        self.sample_rate = sample_rate
        self.chunk_size = 4000
        self.base_threshold = threshold
        self.threshold = threshold
        self.silence_duration = silence_duration
        self.min_record_time = min_record_time
        
        self.noise_floor = 0.005
        self.noise_samples = []
        self.noise_calibration_frames = 8
        self.is_calibrated = False
        self.last_calibration_time = 0
        self.recalibration_interval = 60
        
        self.vosk_model = _load_vosk_model()
        self.recognizer = None
        if self.vosk_model:
            self.recognizer = KaldiRecognizer(self.vosk_model, sample_rate)
        
        self.audio = pyaudio.PyAudio()
        self.stream = None
        self.audio_queue = queue.Queue()
        
        self.frames = []
        # Pre-buffer: 3 seconds
        self.pre_buffer_frames = int(1.0 * sample_rate / self.chunk_size)
        
        self._calibrate_at_startup()
    
    def _audio_callback(self, in_data, frame_count, time_info, status):
        if in_data:
            self.audio_queue.put(in_data)
        return (None, pyaudio.paContinue)
    
    def _calibrate_at_startup(self):
        print("[STT] Calibrating noise level...")
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
                self.threshold = max(self.base_threshold, (self.noise_floor + np.std(samples)) * 2.5)
                self.threshold = max(0.004, min(0.1, self.threshold))
                print(f"[STT] Noise calibrated: floor={self.noise_floor:.4f}, threshold={self.threshold:.4f}")
            self.is_calibrated = True
            self.last_calibration_time = time.time()
        except Exception as e:
            print(f"[STT] Calibration error: {e}")
            self.is_calibrated = True
            
    def _calibrate_noise(self, audio_bytes: bytes) -> bool:
        audio_array = np.frombuffer(audio_bytes, dtype=np.int16)
        energy = np.mean(np.abs(audio_array)) / 32768.0
        self.noise_samples.append(energy)
        if len(self.noise_samples) >= self.noise_calibration_frames:
            self.noise_floor = np.mean(self.noise_samples)
            self.threshold = max(self.base_threshold, (self.noise_floor + np.std(self.noise_samples)) * 2.5)
            self.threshold = max(0.004, min(0.1, self.threshold))
            self.is_calibrated = True
            self.last_calibration_time = time.time()
            return True
        return False
        
    def _update_noise_floor(self, audio_bytes: bytes):
        current_time = time.time()
        if current_time - self.last_calibration_time > self.recalibration_interval:
            audio_array = np.frombuffer(audio_bytes, dtype=np.int16)
            energy = np.mean(np.abs(audio_array)) / 32768.0
            if energy < self.threshold * 0.5:
                self.noise_floor = self.noise_floor * 0.9 + energy * 0.1
                self.threshold = max(self.base_threshold, self.noise_floor * 2.5)
                self.threshold = max(0.004, min(0.1, self.threshold))
                self.last_calibration_time = current_time

    def _is_voice(self, audio_bytes: bytes) -> bool:
        audio_array = np.frombuffer(audio_bytes, dtype=np.int16)
        energy = np.mean(np.abs(audio_array)) / 32768.0
        if energy > self.threshold:
            if not stop_event.is_set(): 
                stop_event.set()
            return True
        return False

    def save_recording(self, filename: Optional[str] = None) -> str:
        if not self.frames: raise ValueError("No audio data to save!")
        if not filename: filename = f"recording_{datetime.now().strftime('%Y%m%d_%H%M%S')}.wav"
        if not filename.endswith('.wav'): filename += '.wav'
        save_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "recordings")
        os.makedirs(save_dir, exist_ok=True)
        fullpath = os.path.join(save_dir, filename)
        wav.write(fullpath, self.sample_rate, np.frombuffer(b''.join(self.frames), dtype=np.int16))
        return fullpath

    def record(self, timeout: Optional[float] = None) -> Tuple[bool, Optional[str]]:
        self.frames = []
        while not self.audio_queue.empty():
            try: self.audio_queue.get_nowait()
            except queue.Empty: break
            
        if self.recognizer:
            self.recognizer = KaldiRecognizer(self.vosk_model, self.sample_rate)
            
        start_time = time.time()
        recording = False
        silent_chunks = 0
        recorded_chunks = 0
        
        max_silent_chunks = int(self.silence_duration * self.sample_rate / self.chunk_size)
        min_recorded_chunks = int(self.min_record_time * self.sample_rate / self.chunk_size)
        
        last_partial = ""
        pre_buffer = [] 
        
        try:
            self.stream = self.audio.open(
                format=pyaudio.paInt16, channels=1, rate=self.sample_rate,
                input=True, frames_per_buffer=self.chunk_size,
                stream_callback=self._audio_callback
            )
            self.stream.start_stream()
            
            while True:
                if timeout is not None:
                    if not recording and (time.time() - start_time > timeout): return False, None
                    if recording and (time.time() - start_time > 60):
                        print()
                        return True, self.save_recording()
                
                try: data = self.audio_queue.get(timeout=0.1)
                except queue.Empty: continue
                
                if not self.is_calibrated:
                    self._calibrate_noise(data)
                    continue
                
                is_voice_now = self._is_voice(data)
                
                if not recording:
                    if is_voice_now:
                        recording = True
                        recorded_chunks = 0
                        silent_chunks = 0
                        self.frames = list(pre_buffer) + [data]
                        if self.recognizer:
                            for pre_data in pre_buffer[-int(1.5 * self.sample_rate / self.chunk_size):]:  # Apply latest 1.5s to vosk
                                self.recognizer.AcceptWaveform(pre_data)
                        print("\r[Listening] ", end="", flush=True)
                    else:
                        self._update_noise_floor(data)
                        pre_buffer.append(data)
                        if len(pre_buffer) > self.pre_buffer_frames: pre_buffer.pop(0)
                    continue
                
                self.frames.append(data)
                recorded_chunks += 1
                
                if self.recognizer:
                    if self.recognizer.AcceptWaveform(data):
                        result = json.loads(self.recognizer.Result())
                        if text := result.get("text", ""):
                            print(f"\r>> {text}                    ", end="", flush=True)
                            last_partial = ""
                    else:
                        partial_text = json.loads(self.recognizer.PartialResult()).get("partial", "")
                        if partial_text and partial_text != last_partial:
                            last_partial = partial_text
                            print(f"\r>> {partial_text}          ", end="", flush=True)
                
                if not is_voice_now:
                    silent_chunks += 1
                    if silent_chunks >= max_silent_chunks:
                        if recorded_chunks >= min_recorded_chunks:
                            print()
                            self.stream.stop_stream()
                            return True, self.save_recording()
                        else:
                            recording = False
                            silent_chunks = 0
                            pre_buffer = self.frames[-self.pre_buffer_frames:] if len(self.frames) > self.pre_buffer_frames else self.frames
                            self.frames = []
                            print("\r[Listening] ", end="", flush=True)
                else:
                    silent_chunks = 0
                    
        except KeyboardInterrupt:
            print("\n[Interrupted]")
            if recording and self.frames: return True, self.save_recording()
            return False, None
        except Exception as e:
            print(f"\n[Error] {e}")
            return False, None
        finally:
            if self.stream:
                try:
                    self.stream.stop_stream()
                    self.stream.close()
                except: pass

    def __del__(self):
        try:
            if self.stream: self.stream.close()
            self.audio.terminate()
        except: pass

# For testing
if __name__ == "__main__":
    print("=" * 50)
    print("Testing Hybrid STT (Vosk live + Whisper final)")
    print("=" * 50)
    print("\nSpeak something... (stops after 2s silence)")
    
    recorder = VoiceRecorder()
    success, filename = recorder.record(timeout=30)
    
    if success:
        print(f"\nSaved to: {filename}")
        print("\n[Sending to Whisper for accurate transcription...]")
        text = transcribe_whisper(filename)
        print(f"\n[Final Whisper Result]: {text}")
    else:
        print("No recording made")
