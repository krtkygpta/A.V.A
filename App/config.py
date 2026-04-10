import os
import json

env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'settings.json')
try:
    with open(env_path, 'r') as _f:
        os.environ.update({k: str(v) for k, v in json.load(_f).items()})
except Exception:
    pass

USER_NAME = os.getenv("USER_NAME", "Kartikey")
ASSISTANT_NAME = os.getenv("ASSISTANT_NAME", "AVA")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
