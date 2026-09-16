from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")
_settings_path = ROOT_DIR / "Server" / "settings.json"

# Keys that may be JSON arrays in settings.json (joined for env).
_LIST_SETTINGS = frozenset(
    {
        "OPENAI_API_KEYS",
        "OPENAI_API_KEY_POOL",
    }
)


def _load_settings_file() -> None:
    try:
        with open(_settings_path, "r", encoding="utf-8") as _f:
            data = json.load(_f)
    except Exception:
        return
    if not isinstance(data, dict):
        return
    for k, v in data.items():
        if v is None:
            continue
        if isinstance(v, list):
            # Comma-join for KeyRotator / env consumers
            os.environ[str(k)] = ",".join(str(x).strip() for x in v if str(x).strip())
        elif isinstance(v, bool):
            os.environ[str(k)] = "true" if v else "false"
        else:
            os.environ[str(k)] = str(v)


_load_settings_file()


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    model_name: str
    openai_api_key: str | None
    openai_base_url: str | None
    openai_api_keys: tuple[str, ...]  # multi-key pool for main LLM
    tts_voice: str
    tts_host: str
    tts_port: int
    tts_startup_timeout: float
    conversation_dir: Path
    groq_api_key: str | None
    google_ai_api_key: str | None
    tavily_api_key: str | None


def get_settings() -> Settings:
    from services.key_rotator import collect_openai_keys, normalize_openai_base_url

    keys = tuple(collect_openai_keys(env=True))
    primary = keys[0] if keys else os.getenv("OPENAI_API_KEY")
    return Settings(
        host=os.getenv("AVA_SERVER_HOST", "127.0.0.1"),
        port=int(os.getenv("AVA_SERVER_PORT", "8765")),
        model_name=os.getenv("MODEL_NAME", ""),
        openai_api_key=primary,
        openai_base_url=normalize_openai_base_url(os.getenv("OPENAI_BASE_URL")),
        openai_api_keys=keys,
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
