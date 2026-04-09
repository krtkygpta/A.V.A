from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(dotenv_path=ROOT_DIR / ".env")


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



def get_settings() -> Settings:
    return Settings(
        host=os.getenv("AVA_SERVER_HOST", "127.0.0.1"),
        port=int(os.getenv("AVA_SERVER_PORT", "8765")),
        model_name=os.getenv("MODEL_NAME", ""),
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        openai_base_url=os.getenv("OPENAI_BASE_URL"),
        tts_voice=os.getenv("AVA_TTS_VOICE", "en_US-hfc_female-medium"),
        tts_host=os.getenv("AVA_LOCAL_PIPER_HOST", "127.0.0.1"),
        tts_port=int(os.getenv("AVA_LOCAL_PIPER_PORT", "5000")),
        tts_startup_timeout=float(os.getenv("AVA_LOCAL_PIPER_TIMEOUT", "60")),
        conversation_dir=Path(os.getenv("AVA_SERVER_CONVERSATIONS_DIR", ROOT_DIR / "Server" / "data" / "conversations")),
    )
