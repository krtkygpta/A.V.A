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

        # If .onnx exists, normalize self.voice directly to the .onnx file path
        if voice_path.suffix != ".onnx":
            candidate = voice_path.with_suffix(".onnx")
            if candidate.exists():
                voice_path = candidate
        self.voice = voice_path

    def _is_piper_responding(self, timeout: float = 2.0) -> bool:
        """Check if Piper HTTP server is already responding to synthesis pings."""
        try:
            response = requests.post(
                f"{self.base_url}/", json={"text": "hi"}, timeout=timeout
            )
            return response.status_code in (200, 400)
        except Exception:
            return False

    def _download_voice_if_missing(self) -> None:
        """Download voice model if the configured path doesn't exist."""
        # Check if the voice path (or with .onnx extension) exists
        onnx_path = (
            self.voice.with_suffix(".onnx")
            if self.voice.suffix and self.voice.suffix != ".onnx"
            else self.voice.with_name(self.voice.name + ".onnx")
        )

        if onnx_path.exists() or self.voice.exists():
            if onnx_path.exists():
                self.voice = onnx_path
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
        elif onnx_path.exists():
            self.voice = onnx_path

    def _free_port_if_in_use(self) -> None:
        """Check if self.port is bound and attempt to clean up stale processes occupying it."""
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.5)
        is_in_use = sock.connect_ex((self.host, self.port)) == 0
        sock.close()
        if not is_in_use:
            return

        print(f"[ServerTTS] Port {self.port} is already in use by another process. Checking...")
        if sys.platform == "win32":
            try:
                import os
                output = subprocess.check_output(
                    f"netstat -ano -p tcp | findstr :{self.port}",
                    shell=True,
                    text=True,
                )
                for line in output.strip().splitlines():
                    parts = line.split()
                    if len(parts) >= 5 and "LISTENING" in line.upper():
                        pid = parts[-1]
                        if pid.isdigit() and int(pid) != os.getpid():
                            print(f"[ServerTTS] Terminating stale listener PID {pid} on port {self.port}...")
                            subprocess.run(["taskkill", "/F", "/T", "/PID", pid], capture_output=True)
                time.sleep(1.0)
            except Exception as e:
                print(f"[ServerTTS] Note: Could not auto-terminate process on port {self.port}: {e}")

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
            # Check if subprocess exited unexpectedly
            if self._server_proc and self._server_proc.poll() is not None:
                err_msg = ""
                if self._server_proc.stderr:
                    try:
                        err_msg = self._server_proc.stderr.read().decode(errors="replace")
                    except Exception:
                        pass
                print(f"[ServerTTS] Piper process exited with code {self._server_proc.returncode}.")
                if err_msg:
                    print(f"[ServerTTS] Stderr:\n{err_msg.strip()}")
                return False

            if self._is_piper_responding(timeout=2.0):
                return True
            time.sleep(0.5)
        return False

    def start(self) -> bool:
        if self._server_proc and self._server_proc.poll() is None:
            return True

        # Check if a responsive Piper server is already running on the configured port
        if self._is_piper_responding(timeout=1.5):
            print(f"[ServerTTS] Piper engine is already running and responsive at {self.base_url}")
            return True

        # Free port if held by a dead/unresponsive zombie process
        self._free_port_if_in_use()

        self._download_voice_if_missing()
        self._server_proc = self._start_piper_process()

        if not self._wait_until_ready():
            print("[ServerTTS] Piper server failed to start.")
            self.stop()
            return False
        return True

    def stop(self) -> None:
        if self._server_proc and self._server_proc.poll() is None:
            try:
                if sys.platform == "win32":
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(self._server_proc.pid)], capture_output=True)
                else:
                    self._server_proc.terminate()
                    self._server_proc.wait(timeout=2)
            except Exception:
                try:
                    self._server_proc.kill()
                except Exception:
                    pass
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
