from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
_settings_path = ROOT_DIR / "Server" / "settings.json"
try:
    with open(_settings_path, "r") as _f:
        os.environ.update({k: str(v) for k, v in json.load(_f).items()})
except Exception:
    pass


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    model_name: str
    openai_api_key: str | None
    openai_base_url: str | None
    tts_voice: str
    tts_host: str
    tts_port: int
    tts_startup_timeout: float
    conversation_dir: Path
    groq_api_key: str | None
    google_ai_api_key: str | None
    tavily_api_key: str | None


def get_settings() -> Settings:
    return Settings(
        host=os.getenv("AVA_SERVER_HOST", "127.0.0.1"),
        port=int(os.getenv("AVA_SERVER_PORT", "8765")),
        model_name=os.getenv("MODEL_NAME", ""),
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        openai_base_url=os.getenv("OPENAI_BASE_URL"),
        tts_voice=os.getenv("tts_voice", "./assets/en_US-hfc_female-medium"),
        tts_host=os.getenv("AVA_LOCAL_PIPER_HOST", "127.0.0.1"),
        tts_port=int(os.getenv("AVA_LOCAL_PIPER_PORT", "5000")),
        tts_startup_timeout=float(os.getenv("AVA_LOCAL_PIPER_TIMEOUT", "60")),
        conversation_dir=Path(
            os.getenv(
                "AVA_SERVER_CONVERSATIONS_DIR",
                ROOT_DIR / "Server" / "data" / "conversations",
            )
        ),
        groq_api_key=os.getenv("GROQ_API_KEY"),
        google_ai_api_key=os.getenv("GOOGLE_AI_API_KEY"),
        tavily_api_key=os.getenv("TAVILY_API_KEY"),
    )
