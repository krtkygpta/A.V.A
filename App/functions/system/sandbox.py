import os
import requests

SERVER_URL = os.getenv("AVA_SERVER_URL", "http://127.0.0.1:8765").rstrip("/")
DEFAULT_TIMEOUT = float(os.getenv("AVA_SERVER_TIMEOUT", "4"))

def run_code_in_sandbox(code: str, timeout: int = 5) -> str:
    """
    Execute Python code via the server API with automatic local fallback.
    Returns combined stdout and stderr as a single string.
    """
    try:
        response = requests.post(
            f"{SERVER_URL}/tools/code_execute",
            json={"code": code, "timeout": timeout},
            timeout=max(DEFAULT_TIMEOUT, timeout + 5.0)
        )
        response.raise_for_status()
        result = response.json()
        if result.get("status") == "success":
            return result.get("content", "[no output]")
        else:
            return f"[error] {result.get('content', 'Unknown error')}"
    except Exception as e:
        # Fallback to local execution if server is unreachable or reports an error
        try:
            import subprocess
            import sys
            import tempfile

            with tempfile.NamedTemporaryFile(delete=False, suffix=".py", mode="w", encoding="utf-8") as f:
                f.write(code)
                f.flush()
                fname = f.name
            try:
                res = subprocess.run(
                    [sys.executable, fname],
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
                output = (res.stdout or "") + ("\n[stderr]\n" + res.stderr if res.stderr else "")
                return output.strip() or "[no output]"
            except subprocess.TimeoutExpired:
                return f"[error] Code execution timed out after {timeout} seconds"
            finally:
                try:
                    os.remove(fname)
                except Exception:
                    pass
        except Exception as local_err:
            return f"[error] Code execution failed (server: {e}; local: {local_err})"