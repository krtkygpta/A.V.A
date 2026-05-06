from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import requests


class TTSService:
    """Server-owned Piper HTTP process + synthesis facade."""

    def __init__(
        self, voice: str, host: str, port: int, startup_timeout: float, root_dir: Path
    ):
        self.host = host
        self.port = port
        self.startup_timeout = startup_timeout
        self.root_dir = root_dir
        self.base_url = f"http://{host}:{port}"
        self._server_proc: subprocess.Popen | None = None

        # Resolve voice path relative to root_dir if relative
        voice_path = Path(voice)
        if not voice_path.is_absolute():
            # If it looks like a file path (has extension or path separators), resolve relative to root_dir
            voice_path = root_dir / voice_path
        self.voice = voice_path

    def _download_voice_if_missing(self) -> None:
        """Download voice model if the configured path doesn't exist."""
        # Check if the voice path (or with .onnx extension) exists
        onnx_path = (
            self.voice.with_suffix(".onnx")
            if self.voice.suffix and self.voice.suffix != ".onnx"
            else self.voice.with_name(self.voice.name + ".onnx")
        )

        if onnx_path.exists() or self.voice.exists():
            print(f"[ServerTTS] Voice found at {self.voice}")
            return

        voice_name = self.voice.stem
        print(f"[ServerTTS] Voice not found, downloading '{voice_name}'...")
        result = subprocess.run(
            [sys.executable, "-m", "piper.download_voices", voice_name],
            capture_output=True,
        )
        if result.returncode != 0:
            print(f"[ServerTTS] Voice download exited with {result.returncode}")
            print(
                f"[ServerTTS] stderr: {result.stderr.decode() if result.stderr else 'none'}"
            )

    def _start_piper_process(self) -> subprocess.Popen:
        return subprocess.Popen(
            [
                sys.executable,
                "-m",
                "piper.http_server",
                "-m",
                str(self.voice),
                "--host",
                self.host,
                "--port",
                str(self.port),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

    def _wait_until_ready(self) -> bool:
        deadline = time.time() + self.startup_timeout
        while time.time() < deadline:
            try:
                response = requests.post(
                    f"{self.base_url}/", json={"text": "hi"}, timeout=15
                )
                if response.status_code in (200, 400):
                    return True
            except requests.RequestException:
                pass
            time.sleep(0.5)
        return False

    def start(self) -> bool:
        if self._server_proc and self._server_proc.poll() is None:
            return True

        self._download_voice_if_missing()
        self._server_proc = self._start_piper_process()

        if not self._wait_until_ready():
            print("[ServerTTS] Piper server failed to start.")
            self.stop()
            return False
        return True

    def stop(self) -> None:
        if self._server_proc and self._server_proc.poll() is None:
            self._server_proc.terminate()
            self._server_proc.wait()
        self._server_proc = None

    def synthesize_bytes(self, text: str) -> bytes | None:
        try:
            response = requests.post(
                f"{self.base_url}/",
                json={"text": text},
                timeout=45,
            )
            response.raise_for_status()
            return response.content
        except requests.RequestException as exc:
            print(f"[ServerTTS] Synthesis error: {exc}")
            return None
