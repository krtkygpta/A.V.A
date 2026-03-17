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

# ── Resolve all paths once, relative to the project root ──
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # A.V.A/
PIPER_EXE    = os.path.join(_PROJECT_ROOT, 'utils', 'piper', 'piper.exe')
MODEL_FILE   = os.path.join(_PROJECT_ROOT, 'data', 'models', 'friday.onnx')
CONFIG_FILE  = os.path.join(_PROJECT_ROOT, 'data', 'models', 'friday.json')
SPEECH_FILE  = os.path.join(_PROJECT_ROOT, 'Speech.wav')
TEMP_INPUT   = os.path.join(_PROJECT_ROOT, 'temp_input.txt')


def lower_active_audio_sessions():
    """
    Lowers the volume of all currently active audio sessions, except for the one
    running this script itself. The volume is reduced by 90% of its current value,
    but never mutes completely.
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
    with wave.open(filename, 'w') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(44100)
        wf.setnframes(0)
        wf.writeframes(b'')


def restore_volume():
    """
    Restores the volume of all currently active audio sessions, except for the one
    running this script itself, to its original level.
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
    Preprocesses the given string to make it suitable for text-to-speech.
    """
    text = re.sub(r'\.(?=\S)', ' point', text)
    text = re.sub(r"[*`#_~]", '', text)
    text = text.replace('&', ' and ')
    text = re.sub(r'[^\w\s]', ' ', text)
    text = re.sub(r'\s+', ' ', text)
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
            pygame.mixer.init()
            pygame.mixer.music.load(SPEECH_FILE)
            pygame.mixer.music.play()

            # Wait until the sound finishes playing or stop is requested
            while pygame.mixer.music.get_busy():
                if stop_event.is_set():
                    pygame.mixer.music.stop()
                    break
                time.sleep(0.01)  # avoid tight CPU spin

            pygame.mixer.music.unload()
            pygame.mixer.quit()
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