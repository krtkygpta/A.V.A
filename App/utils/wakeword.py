"""
wakeword.py - Lightweight wake word detection using Vosk

Detects wake words: "ava", "assistant"
Uses Vosk for fast, offline, low-resource speech recognition.
Optimized for faster response and better noise handling.
"""

import json
import queue
import time
import numpy as np
import sounddevice as sd
from vosk import Model, KaldiRecognizer
import os
import threading

from core.AppStates import wakeword_stop_event

# Wake words to detect (including common mishearings)
WAKE_WORDS = {"ava", "assistant"}
# Similar sounding words that should also trigger
WAKE_WORD_VARIANTS = {
    "ava": ["ava", "eva", "ever", "ava"],
    "assistant": ["assistant", "assist", "assistance"]
}

# Path to Vosk model (small English model)
MODEL_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "vosk-model")


class WakeWordDetector:
    """
    Lightweight wake word detector using Vosk.
    Optimized for faster response with adaptive noise handling.
    """
    
    def __init__(self, model_path: str = None, sample_rate: int = 16000):
        self.sample_rate = sample_rate
        self.audio_queue = queue.Queue()
        
        # Smaller blocksize for faster response (was 4000)
        self.blocksize = 2000
        
        # Adaptive noise floor
        self.noise_floor = 0.005
        self.noise_samples = []
        self.noise_calibration_frames = 10
        self.is_calibrated = False
        
        # Load Vosk model
        model_path = model_path or MODEL_PATH
        if not os.path.exists(model_path):
            raise FileNotFoundError(
                f"Vosk model not found at {model_path}. "
                f"Download from https://alphacephei.com/vosk/models and extract to {model_path}"
            )
        
        print("[WakeWord] Loading Vosk model...")
        self.model = Model(model_path)
        self.recognizer = KaldiRecognizer(self.model, sample_rate)
        # Enable partial results for faster detection
        self.recognizer.SetWords(True)
        print("[WakeWord] Model loaded. Listening for wake words...")
    
    def _audio_callback(self, indata, frames, time_info, status):
        """Callback for audio stream - puts audio data in queue."""
        if status:
            print(f"[WakeWord] Audio status: {status}")
        self.audio_queue.put(bytes(indata))
    
    def _calibrate_noise(self, audio_bytes: bytes) -> bool:
        """Calibrate noise floor from ambient audio."""
        if self.is_calibrated:
            return True
        
        audio_array = np.frombuffer(audio_bytes, dtype=np.int16)
        energy = np.mean(np.abs(audio_array)) / 32768.0
        self.noise_samples.append(energy)
        
        if len(self.noise_samples) >= self.noise_calibration_frames:
            # Set noise floor as 1.5x the average ambient noise
            self.noise_floor = np.mean(self.noise_samples) * 1.5
            # Clamp to reasonable bounds
            self.noise_floor = max(0.003, min(0.05, self.noise_floor))
            self.is_calibrated = True

            return True
        return False
    
    def _has_voice_activity(self, audio_bytes: bytes) -> bool:
        """Quick check if audio has voice activity above noise floor."""
        audio_array = np.frombuffer(audio_bytes, dtype=np.int16)
        energy = np.mean(np.abs(audio_array)) / 32768.0
        # Lower multiplier (1.5x) for better sensitivity
        return energy > self.noise_floor * 1.5
    
    def _check_wake_word(self, text: str) -> str | None:
        """Check if text contains any wake word or variant."""
        text_lower = text.lower()
        for base_word, variants in WAKE_WORD_VARIANTS.items():
            for variant in variants:
                if variant in text_lower:
                    return base_word
        return None
    
    def listen_for_wakeword(self) -> bool:
        """
        Listen continuously until a wake word is detected.
        Returns True when wake word is heard.
        Optimized for faster response time.
        """
        # Clear any old audio in queue
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
            except queue.Empty:
                break
        
        # Reset calibration for fresh noise floor
        if not self.is_calibrated:
            self.noise_samples = []
        
        last_partial = ""
        partial_stable_count = 0
        
        with sd.RawInputStream(
            samplerate=self.sample_rate,
            blocksize=self.blocksize,  # Smaller for faster response
            dtype='int16',
            channels=1,
            callback=self._audio_callback
        ):
            while True:
                try:
                    # Shorter timeout for faster response
                    data = self.audio_queue.get(timeout=0.1)
                except queue.Empty:
                    continue
                
                # Check for external stop request (e.g., mode switch)
                if wakeword_stop_event.is_set():
                    wakeword_stop_event.clear()
                    return False
                
                # Calibrate noise floor from initial frames
                if not self.is_calibrated:
                    self._calibrate_noise(data)
                    continue
                
                # Feed audio to Vosk FIRST (required for recognition to work)
                is_final = self.recognizer.AcceptWaveform(data)
                
                # Check for final result (complete phrase)
                if is_final:
                    result = json.loads(self.recognizer.Result())
                    text = result.get("text", "").lower()
                    
                    if text:
                        detected = self._check_wake_word(text)
                        if detected:
                            print(f"[WakeWord] Detected: '{detected}' in '{text}'")
                            return True
                else:
                    # Check partial results for faster response
                    partial = json.loads(self.recognizer.PartialResult())
                    partial_text = partial.get("partial", "").lower()
                    
                    if partial_text:
                        detected = self._check_wake_word(partial_text)
                        if detected:
                            # If same partial seen multiple times, trigger immediately
                            if partial_text == last_partial:
                                partial_stable_count += 1
                                if partial_stable_count >= 2:
                                    print(f"[WakeWord] Detected (fast): '{detected}'")
                                    self.recognizer.Reset()
                                    return True
                            else:
                                last_partial = partial_text
                                partial_stable_count = 1
                                # Trigger on first detection if confident
                                if len(partial_text.split()) <= 3:
                                    print(f"[WakeWord] Detected (partial): '{detected}'")
                                    self.recognizer.Reset()
                                    return True
                    else:
                        # Reset partial tracking when no speech
                        last_partial = ""
                        partial_stable_count = 0
    
    def reset(self):
        """Reset the recognizer for a fresh start."""
        self.recognizer.Reset()
        # Clear audio queue
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
            except queue.Empty:
                break
        # Keep calibration - recalibrate only if environment changes significantly


# Singleton instance for easy import
_detector = None


def get_detector() -> WakeWordDetector:
    """Get or create the wake word detector singleton."""
    global _detector
    if _detector is None:
        _detector = WakeWordDetector()
    return _detector


def wait_for_wakeword() -> bool:
    """
    Convenience function to wait for wake word.
    Returns True when wake word is detected.
    """
    detector = get_detector()
    return detector.listen_for_wakeword()


if __name__ == "__main__":
    # Test the wake word detector
    print("Testing wake word detection...")
    print(f"Say one of: {WAKE_WORDS}")
    
    detector = WakeWordDetector()
    while True:
        if detector.listen_for_wakeword():
            print("Wake word detected! Listening for command...")
            # Simulate listening for command
            time.sleep(2)
            print("Back to listening for wake word...")
