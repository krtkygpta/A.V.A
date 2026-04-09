from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import requests


class TTSService:
    """Server-owned Piper HTTP process + synthesis facade."""

    def __init__(self, voice: str, host: str, port: int, startup_timeout: float, root_dir: Path):
        self.voice = voice
        self.host = host
        self.port = port
        self.startup_timeout = startup_timeout
        self.root_dir = root_dir
        self.base_url = f"http://{host}:{port}"
        self._server_proc: subprocess.Popen | None = None

    def _download_voice_if_missing(self) -> None:
        import glob

        if glob.glob(str(self.root_dir / f"{self.voice}*")):
            return

        result = subprocess.run(
            [sys.executable, "-m", "piper.download_voices", self.voice],
            capture_output=True,
        )
        if result.returncode != 0:
            print(f"[ServerTTS] Voice download exited with {result.returncode}")

    def _start_piper_process(self) -> subprocess.Popen:
        return subprocess.Popen(
            [
                sys.executable,
                "-m",
                "piper.http_server",
                "-m",
                self.voice,
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
                response = requests.post(f"{self.base_url}/", json={"text": "hi"}, timeout=15)
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
