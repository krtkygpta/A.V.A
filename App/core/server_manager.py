"""
server_manager.py — Automatic detection and background lifecycle management for AVA Server.

Enables the client (TUI, Voice, etc.) to detect if the AVA Server (port 8765)
is running, and automatically spawn it locally if it's offline.
"""

from __future__ import annotations

import atexit
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests

from core.server_api import SERVER_URL

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_SERVER_DATA_DIR = _PROJECT_ROOT / "Server" / "data"
_SERVER_LOG_FILE = _SERVER_DATA_DIR / "server.log"
_SERVER_PID_FILE = _SERVER_DATA_DIR / "server.pid"

# Track process if spawned by this client session
_spawned_process: Optional[subprocess.Popen] = None


def is_server_alive(server_url: Optional[str] = None, timeout: float = 1.0) -> bool:
    """
    Check if the AVA Server is reachable and healthy via GET /health.
    """
    url = (server_url or SERVER_URL).rstrip("/")
    try:
        resp = requests.get(f"{url}/health", timeout=timeout)
        if resp.status_code == 200:
            data = resp.json()
            return data.get("status") == "ok"
    except Exception:
        pass
    return False


def is_local_server(server_url: Optional[str] = None) -> bool:
    """
    Return True if the configured SERVER_URL points to a local address.
    """
    url = server_url or SERVER_URL
    try:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()
        return hostname in ("127.0.0.1", "localhost", "0.0.0.0", "::1")
    except Exception:
        return False


def start_local_server(wait_timeout: float = 25.0, quiet: bool = False) -> bool:
    """
    Spawn the AVA Server locally in the background and wait until it is healthy.
    """
    global _spawned_process

    if is_server_alive(timeout=0.8):
        if not quiet:
            print(f"[Server] AVA Server is already running at {SERVER_URL}")
        return True

    if not is_local_server():
        if not quiet:
            print(f"[Server] Configured SERVER_URL ({SERVER_URL}) is remote. Cannot auto-start locally.")
        return False

    _SERVER_DATA_DIR.mkdir(parents=True, exist_ok=True)

    if not quiet:
        print(f"[Server] Server not detected at {SERVER_URL}. Launching local AVA Server...")

    creation_flags = 0
    if sys.platform == "win32":
        # Detach and avoid opening a separate console window
        CREATE_NO_WINDOW = 0x08000000
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        creation_flags = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP

    try:
        # Open log file for stdout and stderr
        log_f = open(_SERVER_LOG_FILE, "a", encoding="utf-8")
        log_f.write(f"\n--- AVA Server starting via auto-launch at {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
        log_f.flush()

        env = os.environ.copy()
        server_dir = str(_PROJECT_ROOT / "Server")
        root_dir = str(_PROJECT_ROOT)
        existing_pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{server_dir}{os.pathsep}{root_dir}" + (f"{os.pathsep}{existing_pythonpath}" if existing_pythonpath else "")

        cmd = [sys.executable, "-m", "Server"]
        proc = subprocess.Popen(
            cmd,
            cwd=str(_PROJECT_ROOT),
            stdout=log_f,
            stderr=subprocess.STDOUT,
            env=env,
            creationflags=creation_flags,
        )
        _spawned_process = proc

        # Record PID
        try:
            _SERVER_PID_FILE.write_text(str(proc.pid), encoding="utf-8")
        except Exception:
            pass

        # Check if user prefers to terminate server when client exits
        persist_server = os.getenv("AVA_PERSIST_SERVER", "true").lower() in ("true", "1", "yes")
        if not persist_server:
            atexit.register(stop_local_server)

        # Poll /health until server is ready
        start_time = time.time()
        while (time.time() - start_time) < wait_timeout:
            # Check if subprocess died unexpectedly
            if proc.poll() is not None:
                err_msg = f"[Server] Failed to start. Subprocess exited with code {proc.returncode}."
                # Read last few lines of log
                try:
                    lines = _SERVER_LOG_FILE.read_text(encoding="utf-8", errors="ignore").splitlines()
                    tail = "\n".join(lines[-10:])
                    err_msg += f"\nLog tail:\n{tail}"
                except Exception:
                    pass
                print(err_msg)
                return False

            if is_server_alive(timeout=0.6):
                elapsed = round(time.time() - start_time, 1)
                if not quiet:
                    print(f"[Server] AVA Server started successfully in {elapsed}s (PID: {proc.pid})")
                return True

            time.sleep(0.4)

        print(f"[Server] Timed out waiting {wait_timeout}s for AVA Server to become ready.")
        return False

    except Exception as e:
        print(f"[Server] Failed to spawn local server: {e}")
        return False


def stop_local_server() -> bool:
    """
    Terminate the local server process if running.
    """
    global _spawned_process

    # First attempt: if we hold the Popen object
    if _spawned_process and _spawned_process.poll() is None:
        try:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(_spawned_process.pid)], capture_output=True)
            else:
                _spawned_process.terminate()
                _spawned_process.wait(timeout=3)
            return True
        except Exception:
            try:
                _spawned_process.kill()
            except Exception:
                pass

    # Second attempt: check PID file
    if _SERVER_PID_FILE.exists():
        try:
            pid_str = _SERVER_PID_FILE.read_text(encoding="utf-8").strip()
            if pid_str.isdigit():
                pid = int(pid_str)
                if sys.platform == "win32":
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
                else:
                    os.kill(pid, 9)
                _SERVER_PID_FILE.unlink(missing_ok=True)
                return True
        except Exception:
            pass

    return False


def ensure_server_running(auto_start: bool = True, wait_timeout: float = 25.0, quiet: bool = False) -> bool:
    """
    Check if server is active; if not, automatically start it if enabled.
    """
    if is_server_alive(timeout=0.8):
        if not quiet:
            print(f"[Server] Connected to active AVA Server at {SERVER_URL}")
        return True

    enabled = os.getenv("AVA_AUTO_START_SERVER", "true").lower() in ("true", "1", "yes")
    if auto_start and enabled:
        return start_local_server(wait_timeout=wait_timeout, quiet=quiet)

    if not quiet:
        print(f"[Server] WARNING: AVA Server is not running at {SERVER_URL}.")
    return False
