import subprocess, tempfile, os, sys

def run_code_in_sandbox(code: str, timeout: int = 5) -> str:
    """
    Runs Python code in a temporary isolated file.
    Returns combined stdout and stderr as a single string.
    """
    with tempfile.NamedTemporaryFile(delete=False, suffix=".py", mode="w") as tmp:
        tmp.write(code)
        tmp.flush()
        filename = tmp.name
    
    try:
        result = subprocess.run(
            [sys.executable, filename],
            capture_output=True,
            text=True,
            timeout=timeout
        )
        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            output += "\n[stderr]\n" + result.stderr
        return output.strip() or "[no output]"
    except subprocess.TimeoutExpired:
        return "[error] Execution timed out"
    finally:
        os.remove(filename)