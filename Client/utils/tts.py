import subprocess
import os
import re
import time
import threading
import pygame
from ctypes import cast, POINTER
from comtypes import CLSCTX_ALL, CoInitialize, CoUninitialize
from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume, ISimpleAudioVolume
import wave
from config import USER_NAME, ASSISTANT_NAME

# ── Resolve all paths once at module load, relative to the project root ──
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # A.V.A/Client/
PIPER_EXE    = os.path.join(_PROJECT_ROOT, 'utils', 'piper', 'piper.exe')
MODEL_FILE   = os.path.join(_PROJECT_ROOT, 'data', 'models', 'friday.onnx')
CONFIG_FILE  = os.path.join(_PROJECT_ROOT, 'data', 'models', 'friday.json')
SPEECH_FILE  = os.path.join(_PROJECT_ROOT, 'Speech.wav')
TEMP_INPUT   = os.path.join(_PROJECT_ROOT, 'temp_input.txt')


def _ensure_mixer():
    """
    Initialize pygame mixer if not already initialized.
    Called once per playback cycle instead of every TTS invocation,
    saving ~50-100ms of overhead per call.
    """
    if not pygame.mixer.get_init():
        pygame.mixer.init()


def lower_active_audio_sessions():
    """
    Lowers the volume of all currently active audio sessions (except Python itself)
    so that TTS speech is clearly audible over music/media.
    Volume is reduced to 10% of current level, never fully muted.
    """
    sessions = AudioUtilities.GetAllSessions()
    for session in sessions:
        if session.Process and session.Process.name() != "python.exe":
            audio_volume = session._ctl.QueryInterface(ISimpleAudioVolume)
            if audio_volume.GetMasterVolume() > 0.0:
                current_volume = audio_volume.GetMasterVolume()
                new_volume = max(0.1, current_volume * 0.1)
                audio_volume.SetMasterVolume(new_volume, None)


def create_empty_wav(filename):
    """Create an empty WAV file (used as fallback when TTS generation fails)."""
    with wave.open(filename, 'w') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(44100)
        wf.setnframes(0)
        wf.writeframes(b'')


def restore_volume():
    """
    Restores the volume of all audio sessions (except Python itself)
    back to their original levels after TTS playback completes.
    Inverse of lower_active_audio_sessions().
    """
    sessions = AudioUtilities.GetAllSessions()
    for session in sessions:
        if session.Process and session.Process.name() != "python.exe":
            audio_volume = session._ctl.QueryInterface(ISimpleAudioVolume)
            current_volume = audio_volume.GetMasterVolume()
            new_volume = min(1.0, current_volume * 10)
            audio_volume.SetMasterVolume(new_volume, None)


def clean_string_for_tts(text: str) -> str:
    """
    Preprocesses text to make it suitable for text-to-speech.
    Removes markdown/special characters, spells out symbols,
    and handles name pronunciation.
    """
    text = re.sub(r'\.(?=\S)', ' point', text)       # "3.14" → "3 point14"
    text = re.sub(r"[*`#_~]", '', text)               # Strip markdown formatting
    text = text.replace('&', ' and ')                  # "&" → "and"
    text = re.sub(r'[^\w\s]', ' ', text)               # Remove remaining special chars
    text = re.sub(r'\s+', ' ', text)                   # Collapse multiple spaces
    text = text.replace(USER_NAME, "kaaar-tee-kayy ").replace(f"{ASSISTANT_NAME}:", "")
    return text.strip()


def _safe_remove(filepath, retries=30, delay=0.05):
    """Try to remove a file, retrying if it's still locked by another thread."""
    for _ in range(retries):
        try:
            os.remove(filepath)
            return True
        except PermissionError:
            time.sleep(delay)
        except FileNotFoundError:
            return True
    return False


def run_tts_command(text: str, stop_event: threading.Event):
    """
    Runs the given text through the Piper TTS engine and plays the resulting audio.

    Flow:
    1. Clean text for TTS compatibility
    2. Write cleaned text to a temp file (unique per thread to avoid conflicts)
    3. Run Piper to generate Speech.wav
    4. Lower other app volumes, play the audio, restore volumes
    5. Clean up temp file

    :param text: The text to convert to speech.
    :param stop_event: A threading.Event that can be used to stop the audio playback.
    """
    CoInitialize()

    # Use a unique temp file per thread so old threads can't delete our input
    my_temp = os.path.join(_PROJECT_ROOT, f'temp_input_{threading.get_ident()}.txt')

    cleaned_string = clean_string_for_tts(text)
    with open(my_temp, 'w', encoding='utf-8') as f:
        f.write(cleaned_string)

    command = f'type "{my_temp}" | "{PIPER_EXE}" -m "{MODEL_FILE}" -c "{CONFIG_FILE}" -f "{SPEECH_FILE}"'

    try:
        # If a previous Speech.wav exists, signal old thread to stop and wait for release
        if os.path.exists(SPEECH_FILE):
            stop_event.set()
            _safe_remove(SPEECH_FILE)

        # Clear the stop signal before generating & playing new audio
        stop_event.clear()

        subprocess.run(command, shell=True, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        if os.path.exists(SPEECH_FILE):
            lower_active_audio_sessions()

            # Initialize mixer only if needed (avoids ~50-100ms overhead per call)
            _ensure_mixer()
            pygame.mixer.music.load(SPEECH_FILE)
            pygame.mixer.music.play()

            # Wait until the sound finishes playing or stop is requested
            while pygame.mixer.music.get_busy():
                if stop_event.is_set():
                    pygame.mixer.music.stop()
                    break
                time.sleep(0.01)  # avoid tight CPU spin

            pygame.mixer.music.unload()
            # NOTE: We no longer call pygame.mixer.quit() here — mixer stays
            # initialized to avoid reinit overhead on the next TTS call.
            restore_volume()
        else:
            create_empty_wav(SPEECH_FILE)
            print("Audio file not found, command might have failed.")

    except subprocess.CalledProcessError as e:
        print(f"Error while running the command: {e}")

    try:
        os.remove(my_temp)
    except Exception:
        pass