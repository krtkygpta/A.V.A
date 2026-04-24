import os
import requests

SERVER_URL = os.getenv("AVA_SERVER_URL", "http://127.0.0.1:8765").rstrip("/")
DEFAULT_TIMEOUT = float(os.getenv("AVA_SERVER_TIMEOUT", "4"))

def run_code_in_sandbox(code: str, timeout: int = 5) -> str:
    """
    Execute Python code via the server API.
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
    except requests.exceptions.RequestException as e:
        return f"[error] Server request failed: {e}"