"""
model_router.py - 5-tier model resolution and configuration for AVA and background workers.

Tiers:
  - low (nano): Fast & budget model for trivial checks and light tasks.
  - medium_low (small): Lightweight model for basic workers and routine summaries.
  - medium (main): Balanced everyday model for main chat and standard tools.
  - high (heavy): High-capability model for deep research and complex coding.
  - very_high (flagship): Best model in the world for critical code, architecture, and self-editing.

Configs are loaded dynamically from App/models_config.json, with fallback to environment variables.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "models_config.json"

CANONICAL_TIERS = ("low", "medium_low", "medium", "high", "very_high")
VALID_TIERS = (
    "none",
    "low",
    "medium_low",
    "medium",
    "high",
    "very_high",
    # Legacy aliases
    "nano",
    "small",
    "main",
    "heavy",
)

TIER_ALIASES = {
    "nano": "low",
    "small": "medium_low",
    "main": "medium",
    "heavy": "high",
    "low": "low",
    "medium_low": "medium_low",
    "mediumlow": "medium_low",
    "medium": "medium",
    "high": "high",
    "very_high": "very_high",
    "veryhigh": "very_high",
    "best": "very_high",
    "flagship": "very_high",
}

# Default tier by task_type
TIER_BY_TASK_TYPE = {
    "timer": "none",
    "demo": "none",
    "scrape": "none",
    "opencode": "none",  # legacy
    "coding": "high",    # native coding agent defaults to high / very_high
    "smarthome": "medium_low",
    "delegate": "medium_low",
    "mission": "high",   # plan/synth; children use medium
    "research": "high",
    "general": "medium",
}

_DIFFICULTY_BUMP = {
    "easy": 0,
    "normal": 0,
    "hard": 1,
}

_TIER_ORDER = ["none", "low", "medium_low", "medium", "high", "very_high"]


def canonicalize_tier(tier: Optional[str]) -> str:
    """Normalize a tier name or alias into a canonical 5-tier name."""
    if not tier:
        return "medium"
    raw = str(tier).strip().lower().replace("-", "_").replace(" ", "_")
    if raw == "none":
        return "none"
    return TIER_ALIASES.get(raw, "medium")


def load_models_config() -> Dict[str, Any]:
    """Load the 5-tier model config from disk, returning default structure on failure."""
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and "tiers" in data:
                    return data
        except Exception as e:
            print(f"[model_router] Failed to read {CONFIG_PATH}: {e}")

    # Fallback defaults
    main_model = os.getenv("MODEL_NAME", "gpt-5.6-luna")
    return {
        "tiers": {
            "low": {
                "name": "Low Tier (Fast / Budget)",
                "model": os.getenv("AVA_MODEL_NANO") or os.getenv("AVA_MODEL_LOW", main_model),
                "temperature": 0.2,
                "max_tokens": 4096,
                "description": "Fast and cheap model for trivial checks and filtering."
            },
            "medium_low": {
                "name": "Medium-Low Tier (Light Worker)",
                "model": os.getenv("AVA_MODEL_SMALL") or os.getenv("AVA_MODEL_MEDIUM_LOW", main_model),
                "temperature": 0.3,
                "max_tokens": 8192,
                "description": "Lightweight model for routine summarization and small helper tasks."
            },
            "medium": {
                "name": "Medium Tier (Balanced Everyday)",
                "model": main_model,
                "temperature": 0.5,
                "max_tokens": 8192,
                "description": "Balanced everyday model for standard conversation and regular tasks."
            },
            "high": {
                "name": "High Tier (Advanced Reasoning & Coding)",
                "model": os.getenv("AVA_MODEL_HEAVY") or os.getenv("AVA_MODEL_HIGH", main_model),
                "temperature": 0.2,
                "max_tokens": 16384,
                "description": "High-capability model for deep research, data analysis, and coding."
            },
            "very_high": {
                "name": "Very High Tier (World-Class / Flagship)",
                "model": os.getenv("AVA_MODEL_VERY_HIGH", main_model),
                "temperature": 0.1,
                "max_tokens": 32768,
                "description": "World-class model for critical software architecture, self-editing, and debugging."
            }
        }
    }


def save_models_config(config: Dict[str, Any]) -> bool:
    """Save updated model config back to App/models_config.json."""
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        print(f"[model_router] Failed to write {CONFIG_PATH}: {e}")
        return False


def get_tier_config(tier: str) -> Dict[str, Any]:
    """Retrieve full configuration dictionary for a given tier."""
    canonical = canonicalize_tier(tier)
    cfg = load_models_config()
    tiers = cfg.get("tiers", {})
    if canonical in tiers:
        return dict(tiers[canonical])
    return dict(tiers.get("medium", {}))


def update_tier_config(tier: str, **updates: Any) -> Dict[str, Any]:
    """Update settings for a tier and persist to disk."""
    canonical = canonicalize_tier(tier)
    cfg = load_models_config()
    tiers = cfg.setdefault("tiers", {})
    current = tiers.setdefault(canonical, {})
    current.update({k: v for k, v in updates.items() if v is not None})
    save_models_config(cfg)
    return current


def infer_model_tier(
    task_type: str = "general",
    difficulty: Optional[str] = None,
    explicit: Optional[str] = None,
) -> str:
    """Resolve model tier with difficulty bumps."""
    if explicit:
        norm = canonicalize_tier(explicit)
        if norm in _TIER_ORDER:
            return norm
    base = TIER_BY_TASK_TYPE.get(task_type or "general", "medium")
    if base == "none":
        return "none"
    base = canonicalize_tier(base)
    bump = _DIFFICULTY_BUMP.get((difficulty or "normal").lower(), 0)
    if bump:
        try:
            idx = min(len(_TIER_ORDER) - 1, _TIER_ORDER.index(base) + bump)
            return _TIER_ORDER[idx]
        except ValueError:
            pass
    return base


def resolve_model_name(tier: str, default_main: Optional[str] = None) -> Optional[str]:
    """
    Map tier -> concrete model id for the OpenAI-compatible API.
    Returns None for tier 'none'.
    """
    tier_norm = (tier or "medium").strip().lower()
    if tier_norm == "none":
        return None

    canonical = canonicalize_tier(tier_norm)
    tier_info = get_tier_config(canonical)
    configured_model = tier_info.get("model")
    if configured_model and str(configured_model).strip():
        return str(configured_model).strip()

    main = (
        default_main
        or os.getenv("AVA_MODEL_MAIN")
        or os.getenv("MODEL_NAME")
        or ""
    ).strip() or None
    return main


def workers_skip_server_context(tier: str = "") -> bool:
    """Background workers should not pull recent_hour_context (token waste)."""
    return True
