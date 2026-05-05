import sys

import sounddevice as sd
import soundfile as sf


def preload(audio_path: str) -> tuple:
    """
    Loads audio file into memory and warms up the audio driver.
    Call this once at startup.

    Returns:
        (audio_data, sample_rate) tuple ready to pass into play()
    """
    print(f"Loading '{audio_path}' ... ", end="", flush=True)
    audio_data, sample_rate = sf.read(audio_path, dtype="float32")
    print("done.")

    # Warm up PortAudio so the first play() has no driver-init overhead
    warmup = sd.OutputStream(
        samplerate=sample_rate, channels=audio_data.ndim if audio_data.ndim > 1 else 1
    )
    warmup.start()
    warmup.stop()
    warmup.close()

    return audio_data, sample_rate


audio_data, sample_rate = preload("./assets/chime.wav")


def play() -> None:
    """
    Plays the preloaded audio immediately (non-blocking).
    """
    sd.play(audio_data, sample_rate)


if __name__ == "__main__":
    print("Press Enter to play  |  Ctrl+C to quit\n")

    try:
        while True:
            input()
            play()
    except KeyboardInterrupt:
        sd.stop()
        print("\nBye!")
        sys.exit(0)
