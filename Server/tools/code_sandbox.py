"""Python code sandbox execution."""

import os
import subprocess
import sys
import tempfile
from typing import Any


class CodeSandbox:
    def execute(self, code: str, timeout: int = 5) -> dict[str, Any]:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".py", mode="w") as f:
            f.write(code)
            f.flush()
            filename = f.name

        try:
            result = subprocess.run(
                [sys.executable, filename],
                capture_output=True, text=True, timeout=timeout
            )
            output = (result.stdout or "") + ("\n[stderr]\n" + result.stderr if result.stderr else "")
            return {"status": "success", "content": output.strip() or "[no output]"}
        except subprocess.TimeoutExpired:
            return {"status": "error", "content": "Execution timed out"}
        except Exception as e:
            return {"status": "error", "content": str(e)}
        finally:
            try: os.remove(filename)
            except: pass