import json
import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
_settings_path = ROOT_DIR / "App" / "settings.json"

try:
    with open(_settings_path, "r") as _f:
        os.environ.update({k: str(v) for k, v in json.load(_f).items()})
except Exception:
    pass

USER_NAME = os.getenv("USER_NAME", "Kartikey")
ASSISTANT_NAME = os.getenv("ASSISTANT_NAME", "AVA")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
